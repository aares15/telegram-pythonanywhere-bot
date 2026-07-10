import hmac
import os
import subprocess

from flask import Flask, request

app = Flask(__name__)

# Project root — used by /api/health to report the deployed commit.
# api/index.py is at <repo>/api/index.py, so two dirname() calls give the root.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _commit_sha() -> str:
    """Short SHA of the running commit, or "" when it can't be determined.

    On Vercel the source is deployed without a usable .git at runtime, so we
    prefer the VERCEL_GIT_COMMIT_SHA env var that Vercel injects at build time.
    Falls back to `git rev-parse` for local dev / other hosts. Computed once at
    import (= cold start), so /api/health reports the code actually RUNNING —
    the definitive "which commit is live?" probe.
    """
    sha = os.environ.get("VERCEL_GIT_COMMIT_SHA", "").strip()
    if sha:
        return sha[:7]
    try:
        result = subprocess.run(
            ["git", "-C", _PROJECT_ROOT, "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return ""


_COMMIT_SHA = _commit_sha()


@app.route("/api/health")
@app.route("/api/index")
def health():
    # Keep this endpoint dependency-free so uptime pings don't trigger
    # Telegram/store/AI client init. Body is "OK <sha>" so one curl
    # answers both "is it up?" and "which commit is live?".
    return ("OK " + _COMMIT_SHA).strip(), 200


# Module-level flags. Serverless functions cold-start per instance and re-run
# module code each time; these live for the life of one warm instance.
_WARNED_NO_WEBHOOK_SECRET = [False]
_BOOTSTRAPPED = [False]


def _bootstrap_once() -> None:
    """Sync the Telegram "/" command menu once per warm instance.

    Vercel has no persistent "boot" step, so this is the serverless analog: the
    first authenticated webhook after a cold start calls register_commands() to
    keep Telegram's "/" menu in step with the current handlers (stale commands
    disappear after they're deleted from the code).

    It deliberately does NOT (re)register the webhook. The webhook is set once by
    hand (see README) and must never be overwritten from inside the request path:
    a missing or stale WEBHOOK_URL env var would silently re-point Telegram away
    from this endpoint and take the whole bot offline.

    Runs at most once per instance (flag set BEFORE the attempt so a persistent
    failure doesn't add latency to every request; a fresh instance retries) and
    never raises — a failure must not drop the user's message.
    """
    if _BOOTSTRAPPED[0]:
        return
    _BOOTSTRAPPED[0] = True
    try:
        from bot.clients import register_commands

        print(register_commands())
    except Exception as e:
        print(f"Command-menu sync failed: {e}")


@app.route("/api/webhook", methods=["POST"])
def webhook():
    # Verify the secret BEFORE any heavy imports. bot.config only reads
    # env vars, no network. bot.clients/handlers/telebot would otherwise
    # trigger bot.get_me() on every cold start — including for forged or
    # mis-secreted POSTs.
    from bot.config import WEBHOOK_SECRET

    if WEBHOOK_SECRET:
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(token, WEBHOOK_SECRET):
            return "Forbidden", 403
    elif not _WARNED_NO_WEBHOOK_SECRET[0]:
        # First-request warning so operators notice the fail-open path.
        # WEBHOOK_SECRET stays optional for backwards compat + local
        # teaching, but anyone running this in production should set it.
        print(
            "WARNING: WEBHOOK_SECRET is not set. /api/webhook accepts "
            "unauthenticated POSTs — anyone who guesses the URL can forge "
            "Telegram updates. Set WEBHOOK_SECRET and re-register the "
            "webhook with secret_token=<your_secret>."
        )
        _WARNED_NO_WEBHOOK_SECRET[0] = True

    # Authenticated — now pull the heavyweight modules.
    import telebot

    import bot.handlers  # noqa: F401 — registers @bot.message_handler decorators
    from bot.clients import bot

    # Sync the "/" command menu once per cold start (Vercel has no boot step).
    # Does NOT touch the webhook — that's set once by hand (see README).
    _bootstrap_once()

    raw = request.get_data(as_text=True)
    try:
        update = telebot.types.Update.de_json(raw)
    except Exception as e:
        print(f"Malformed update: {e}")
        return "Bad Request", 400
    if update is None:
        return "Bad Request", 400

    update_id = getattr(update, "update_id", None)
    if update_id is not None:
        from bot.dedupe import try_acquire

        if not try_acquire(update_id):
            # Already claimed by another delivery or a prior successful run.
            return "OK", 200

    try:
        bot.process_new_updates([update])
    except Exception:
        # Processing crashed — release the dedupe claim so Telegram's
        # retry of this update_id isn't silently dropped, then let the
        # 500 propagate so Telegram knows to retry.
        if update_id is not None:
            from bot.dedupe import release

            release(update_id)
        raise
    return "OK", 200


@app.route("/api/tick", methods=["POST"])
def tick():
    """Daily-quiz broadcast trigger, called on a schedule by
    .github/workflows/daily-quiz.yml.

    Verifies an X-Tick-Secret header against TICK_SECRET (constant-time).
    Fail-closed: returns 403 when TICK_SECRET is unset, so a misconfigured
    deploy can't let anyone spam subscribers. This is a DIFFERENT secret from
    the Telegram webhook secret (X-Telegram-Bot-Api-Secret-Token) — the tick
    endpoint only broadcasts a quiz, nothing more.

    run_daily_quiz() takes an atomic once-per-day store claim, so overlapping or
    retried calls are idempotent. It never raises, so the endpoint returns 200
    with a summary body even for "no subscribers" / "already sent" / "failed".
    """
    from bot.config import TICK_SECRET

    if not TICK_SECRET:
        return "Tick endpoint disabled (TICK_SECRET unset)", 403

    provided = request.headers.get("X-Tick-Secret", "")
    if not hmac.compare_digest(provided, TICK_SECRET):
        return "Forbidden", 403

    from bot.quiz import run_daily_quiz

    summary = run_daily_quiz()
    print(summary)
    return summary + "\n", 200
