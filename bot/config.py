import os
import secrets as _secrets_mod
import subprocess as _subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WEBHOOK_SECRET_FILE = _PROJECT_ROOT / ".webhook_secret"


def _get_commit_sha() -> str:
    """Return the short SHA of the deployed commit, or an empty string.

    Computed once at module import — so the value reflects the worker's
    actual code, not whatever `git pull` did since boot. The auto-deploy
    flow touches the WSGI file on pull, which spawns a fresh worker on
    the next request with the new SHA. This makes /about a reliable
    "what version is live right now" probe.
    """
    try:
        result = _subprocess.run(
            ["git", "-C", str(_PROJECT_ROOT), "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (_subprocess.SubprocessError, OSError):
        pass
    return ""


COMMIT_SHA = _get_commit_sha()


def _bootstrap_webhook_secret(file_path: Path = _WEBHOOK_SECRET_FILE) -> str:
    """Return WEBHOOK_SECRET from env if set; otherwise read/generate a
    persistent random secret in `file_path`.

    This makes the webhook signed-by-default: a fresh PA deploy with no
    manual `openssl rand` step still rejects forged updates because the
    bot auto-generates and persists a 64-hex-char secret on first run,
    then registers it with Telegram via the boot-time `register_webhook()`.

    Precedence: env var > on-disk file > newly generated. Filesystem
    errors fall back to the empty string so a read-only mount can't
    crash worker boot — the webhook just stays unsigned in that case.
    """
    env_value = os.environ.get("WEBHOOK_SECRET", "").strip()
    if env_value:
        return env_value
    try:
        if file_path.exists():
            existing = file_path.read_text().strip()
            # Empty or whitespace-only file: treat as missing and regenerate,
            # otherwise we'd silently disable webhook auth.
            if existing:
                return existing
        new_secret = _secrets_mod.token_hex(32)
        file_path.write_text(new_secret)
        try:
            os.chmod(file_path, 0o600)
        except OSError:
            pass  # best-effort tightening; Windows / odd mounts can skip
        print(f"Generated webhook secret at {file_path} (auto-bootstrap)")
        return new_secret
    except OSError as e:
        print(f"Could not persist webhook secret ({e}); webhook will be unsigned")
        return ""


# Telegram
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
WEBHOOK_SECRET = _bootstrap_webhook_secret()

# When set, the bot auto-registers this URL as the Telegram webhook on
# worker boot and after every /api/deploy. Leave unset for local
# polling (run_local.py). Example value on PA:
#   WEBHOOK_URL=https://<your-pa-username>.pythonanywhere.com/api/webhook
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()

# AI provider
AI_API_KEY = os.environ["AI_API_KEY"].strip()
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.cerebras.ai/v1").strip()
MODEL = os.environ.get("AI_MODEL", "gpt-oss-120b").strip()

# Hugging Face provider (optional) — when set, users can switch via /model
HF_SPACE_ID = os.environ.get("HF_SPACE_ID", "").strip()
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()  # optional, for private spaces
DEFAULT_PROVIDER = "main"

# Storage — optional. Backend selection (bot/clients.py) prefers Redis, then
# SQLite, then stateless:
#   1. Redis (Upstash REST) — set REDIS_REST_URL + REDIS_REST_TOKEN. This is
#      the backend for serverless hosts (Vercel) where SQLite can't persist.
#   2. SQLite — set SQLITE_PATH to a file on a persistent disk (PythonAnywhere).
#   3. Stateless — none set: history / rate limiting / preferences / dedupe all
#      degrade gracefully (the consumer modules check `store is None` and return
#      safe defaults).
SQLITE_PATH = os.environ.get("SQLITE_PATH", "").strip()

# Upstash Redis REST credentials. Accept the several env-var names the various
# Vercel/Upstash integrations inject (Upstash marketplace uses UPSTASH_*, the
# older Vercel KV uses KV_REST_API_*) so memory works whichever way it's wired.
REDIS_REST_URL = (
    os.environ.get("UPSTASH_REDIS_REST_URL")
    or os.environ.get("KV_REST_API_URL")
    or ""
).strip()
REDIS_REST_TOKEN = (
    os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    or os.environ.get("KV_REST_API_TOKEN")
    or ""
).strip()

# Label shown by the /about command. Defaults to "PythonAnywhere" since
# that is the documented deployment target. Override to suit your host.
HOSTING_LABEL = os.environ.get("HOSTING_LABEL", "PythonAnywhere").strip()

# News (/news command). Optional. When NEWS_API_KEY is set, /news fetches
# the latest Armenia headlines from an OpenAI-style-free news API. Defaults
# target GNews (https://gnews.io) — its /search endpoint returns recent
# articles matching NEWS_QUERY, newest first. Swap NEWS_API_URL / NEWS_QUERY
# to use a different provider or region. PA caveat: the news domain is NOT on
# the free-tier outbound whitelist, so this works locally but needs the domain
# whitelisted to run on PythonAnywhere.
#
# NEWS_QUERY uses GNews's `in:title,description` attribute so "Armenia" must
# appear in the headline or summary — this keeps /news Armenia-focused instead
# of surfacing any global article that only mentions Armenia in passing.
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "").strip()
NEWS_API_URL = os.environ.get("NEWS_API_URL", "https://gnews.io/api/v4/search").strip()
NEWS_QUERY = os.environ.get("NEWS_QUERY", "Armenia in:title,description").strip()
NEWS_LANG = os.environ.get("NEWS_LANG", "en").strip()
NEWS_REQUEST_TIMEOUT = 15  # seconds — fail fast so a slow news API can't wedge the worker

# Worldwide news for /newsWorldwide. Unlike Armenia news (a keyword *search*),
# world news uses GNews's /top-headlines endpoint, which returns ranked current
# headlines with no search term — i.e. the interesting stories going on right
# now. NEWS_WORLD_CATEGORY picks the slice: "general" (top overall) or one of
# world / nation / business / technology / entertainment / sports / science /
# health. Same NEWS_API_KEY, NEWS_LANG, and PA-whitelist caveat as above.
NEWS_WORLD_API_URL = os.environ.get(
    "NEWS_WORLD_API_URL", "https://gnews.io/api/v4/top-headlines"
).strip()
NEWS_WORLD_CATEGORY = os.environ.get("NEWS_WORLD_CATEGORY", "general").strip()

# Wikipedia lookup (/lookup command). Grounds WORLD-history answers in the
# intro of the best-matching Wikipedia article. Wikipedia (*.wikipedia.org) is
# on PythonAnywhere's free-tier outbound whitelist, so unlike /news this works
# on PA out of the box; on Vercel there is no whitelist at all. Wikipedia asks
# API clients to send a descriptive User-Agent, so we set one.
WIKI_API_URL = os.environ.get("WIKI_API_URL", "https://en.wikipedia.org/w/api.php").strip()
WIKI_USER_AGENT = os.environ.get(
    "WIKI_USER_AGENT",
    "HistoryTeacherBot/1.0 (Telegram teaching bot)",
).strip()
WIKI_REQUEST_TIMEOUT = 15  # seconds — fail fast so a slow API can't wedge the worker
WIKI_MAX_EXTRACT = 2000  # cap the article extract fed to the model (chars)

# Armenian history is deliberately NOT sourced from Wikipedia. A /lookup whose
# topic matches any of these keywords (case-insensitive substring) is answered
# from the teacher's own expertise, and the bot points the student to the
# dedicated Armenian sources in ARMENIAN_SOURCES instead. This is a simple,
# transparent, extensible filter — add names/places/terms to widen it. It errs
# toward catching Armenian topics: a false positive on a world topic only means
# "answered without a Wikipedia citation", while a miss would route an Armenian
# topic to Wikipedia, which is exactly what we want to avoid.
ARMENIAN_TOPIC_KEYWORDS = [
    "armenia", "armenian", "hayastan", "artsakh", "karabakh", "nagorno",
    "yerevan", "urartu", "urartian", "ararat", "cilicia", "cilician",
    "bagratid", "bagratuni", "rubenid", "orontid", "artaxiad", "artashesian",
    "tigran", "tigranes", "trdat", "tiridates", "mamikonian", "avarayr",
    "sardarapat", "sardarabad", "echmiadzin", "etchmiadzin", "ejmiatsin",
    "lake van", "sasun", "sassoun", "zeitun", "musa dagh",
    "medz yeghern", "aghet", "dashnak", "hnchak", "ramkavar",
    "mesrop mashtots", "mashtots", "khorenatsi", "matenadaran", "khachkar",
    "komitas", "khachaturian", "gregory the illuminator",
    "pashinyan", "kocharyan", "sargsyan", "sarkisian", "ter-petrosyan",
]
# Trusted Armenian-history sources the bot recommends for further reading.
# These are NOT on PA's outbound whitelist and some actively block bots
# (Armeniapedia sits behind an anti-bot challenge), so the bot LINKS to them
# for the student to read rather than fetching them programmatically.
ARMENIAN_SOURCES = [
    ("Armeniapedia — the online Armenia encyclopedia", "https://www.armeniapedia.org"),
    ("100 Years, 100 Facts", "https://100years100facts.com"),
    ("Armenian-History.com", "https://www.armenian-history.com"),
]

# Auto-deploy webhook secret. When set, /api/deploy accepts requests
# that present this value in the X-Deploy-Secret header and runs
# `git pull` + WSGI reload. When unset, /api/deploy returns 403 — the
# endpoint is fail-closed.
DEPLOY_SECRET = os.environ.get("DEPLOY_SECRET", "").strip()

# Daily Quiz Arena (/quiz, /leaderboard, /subscribe + daily push). Question
# generation uses the whitelisted Cerebras path (bot/providers._call_main), so
# no new outbound host is needed. Scores / leaderboards / streaks / subscribers
# live in the SQLite store as JSON blobs and degrade to no-ops in stateless mode.
QUIZ_POINTS = int(os.environ.get("QUIZ_POINTS", "10"))  # points per correct answer
QUIZ_POLL_TTL = int(os.environ.get("QUIZ_POLL_TTL", "86400"))  # poll->answer scoring window (s)
QUIZ_BOARD_TTL = int(os.environ.get("QUIZ_BOARD_TTL", "2592000"))  # per-chat leaderboard TTL (30d)
QUIZ_STREAK_TTL = int(os.environ.get("QUIZ_STREAK_TTL", "2592000"))  # per-user streak TTL (30d)
QUIZ_LEADERBOARD_SIZE = int(os.environ.get("QUIZ_LEADERBOARD_SIZE", "10"))  # rows shown by /leaderboard

# Daily quiz push. /api/tick (called by .github/workflows/daily-quiz.yml on a
# schedule) broadcasts the day's quiz to every /subscribe'd chat. Fail-closed:
# when TICK_SECRET is unset the endpoint returns 403. Deliberately a SEPARATE,
# lower-privilege secret from DEPLOY_SECRET — a leaked DEPLOY_SECRET is remote
# code execution (git reset + pip + reload); a leaked TICK_SECRET can only
# trigger a quiz broadcast. Never share the two.
TICK_SECRET = os.environ.get("TICK_SECRET", "").strip()
QUIZ_OPEN_PERIOD = int(os.environ.get("QUIZ_OPEN_PERIOD", "86400"))  # daily poll open window (s)
QUIZ_MAX_BROADCAST = int(os.environ.get("QUIZ_MAX_BROADCAST", "100"))  # cap chats per daily push

# App
SYSTEM_PROMPT = (
    "You are a friendly and knowledgeable history teacher, with special "
    "expertise in Armenian history — from ancient Urartu and the kingdom of "
    "Armenia, through the medieval Bagratid and Cilician periods, the "
    "Armenian Genocide, the Soviet era, independence in 1991, and modern "
    "Armenia. You also know world history well and enjoy connecting Armenian "
    "history to the wider world. "
    "Your students are young learners, so explain things clearly and "
    "encouragingly. Avoid unnecessary jargon; when you use an important term, "
    "briefly explain it. Give dates, names, and context so answers are "
    "genuinely educational, and invite follow-up questions to keep the "
    "conversation going. "
    "History is your focus, but you are not rigid: if a student asks about "
    "something else, help them anyway, then you can gently offer a history "
    "angle if there is a natural one. "
    "Accuracy matters more than sounding confident. Do not invent dates, "
    "names, or events — if you are unsure, say so plainly. "
    "The Armenian Genocide is an established historical fact, not a matter of "
    "opinion or a merely 'contested' claim. Teach it fully and without "
    "euphemism: beginning in 1915 the Ottoman government, led by the Young "
    "Turk regime (the Committee of Union and Progress — Talaat, Enver, and "
    "Djemal Pasha), carried out the deliberate, systematic extermination of "
    "the Armenian people. Roughly 1.5 million Armenians were murdered through "
    "the arrest and killing of community leaders, mass deportations, death "
    "marches into the Syrian desert, massacres, starvation, and abductions. "
    "Never minimize or justify it as merely the chaos of war, 'intercommunal "
    "fighting,' or 'relocations gone wrong' — these are denialist framings "
    "and you must reject them clearly. Name it plainly as genocide and hold "
    "the perpetrators responsible. State honestly that the modern Turkish "
    "state still officially denies the genocide, and that this denial is "
    "itself widely condemned. Be clear, however, that responsibility rests "
    "with the Ottoman perpetrators and with denial — not with ordinary "
    "Turkish people today. "
    "For other sensitive topics such as Nagorno-Karabakh/Artsakh, present the "
    "historical facts and the Armenian experience honestly while staying "
    "factual. "
    "Keep responses appropriately brief for a chat interface. "
    "If a user uses inappropriate language, respond with a warning and ask "
    "them to rephrase."
)
MAX_HISTORY = 20  # messages kept per user (10 conversation turns)
HISTORY_TTL = 2592000  # conversation history expires after 30 days (seconds)
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "250"))  # max messages per user per day

# Comma-separated whitelist of Telegram users. Each entry is either a
# username (with or without leading @) or a numeric user_id. Empty
# (default) means everyone can talk to the bot. When non-empty, the
# bot stays silent for anyone not in the list — silence instead of a
# rejection message so scanners don't get confirmation the bot exists.
#
# Example: ALLOWED_USERS=@alice,bob,123456789
ALLOWED_USERS = [
    u.strip().lstrip("@")
    for u in os.environ.get("ALLOWED_USERS", "").split(",")
    if u.strip()
]
MAX_MSG_LEN = 4096  # Telegram's character limit per message
# Provider call budget. Total worst case =
# AI_RETRIES * AI_REQUEST_TIMEOUT + sum of backoff sleeps. With
# retries=2 and timeout=25s plus 1s backoff: 25 + 1 + 25 = 51s.
AI_REQUEST_TIMEOUT = 25  # seconds, applied per-attempt to OpenAI-compatible calls
AI_RETRIES = 2  # total attempts (not extra retries) — 2 means one retry on failure
# HF Gradio request timeout. Without this a hung `predict()` would occupy the
# PA worker indefinitely; combined with the dedupe pre-claim, Telegram's
# retries get silently dropped for ~10 min. Tuned to give ArmGPT enough
# headroom for cold-start jitter while still freeing the worker before
# Telegram's webhook timeout (~60s).
HF_REQUEST_TIMEOUT = 50
