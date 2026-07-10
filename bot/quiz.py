"""Daily Quiz Arena — AI-generated trivia with scoring, per-chat leaderboards,
per-user daily streaks, and an optional daily broadcast.

Design notes:
- Question generation goes straight through ``bot.providers._call_main`` (the
  Cerebras / OpenAI-compatible path). We deliberately bypass ``bot.ai.ask_ai``
  so quiz JSON never pollutes a user's
  conversation history, the chat persona system prompt doesn't leak in, and the
  ``/model hf`` preference (ArmGPT, a base model that can't emit JSON) is never
  used for quizzes.
- All state is stored in the KV store as JSON blobs, because ``bot/store.py``
  has no key-scan: one blob per poll, per chat (leaderboard), per user (streak),
  plus a single subscribers list. Read-modify-write is race-safe because the
  webhook is serialized (``threaded=False``, ``max_connections=1``).
- Graceful degradation mirrors ``bot/notes.py``: every store touch guards
  ``store is None`` and is wrapped so a runtime error never raises into a
  handler (safe defaults + a logged line instead).
"""

import json
from datetime import date, timedelta
from typing import Optional

from bot.clients import bot, store
from bot.config import (
    QUIZ_BOARD_TTL,
    QUIZ_LEADERBOARD_SIZE,
    QUIZ_MAX_BROADCAST,
    QUIZ_OPEN_PERIOD,
    QUIZ_POINTS,
    QUIZ_POLL_TTL,
    QUIZ_STREAK_TTL,
)
from bot.providers import _call_main

# ── Storage keys ─────────────────────────────────────────────────────────────
_POLL_KEY = "quiz:poll:{poll_id}"       # {chat_id, correct, points, answered:[uid]}
_BOARD_KEY = "quiz:board:{chat_id}"     # {"<uid>": {name, score}}
_STREAK_KEY = "quiz:streak:{user_id}"   # {last_correct_date, streak_count, best_streak}
_SUBS_KEY = "quiz:subscribers"          # [chat_id, ...]
_TICK_KEY = "quiz:tick:{day}"           # "1" — once-per-day broadcast claim

# Telegram quiz-poll hard limits (send_poll rejects anything past these).
_MAX_QUESTION = 300
_MAX_OPTION = 100
_MAX_EXPLANATION = 200
_MIN_OPTIONS = 2
_MAX_OPTIONS = 10


# ── AI question generation ───────────────────────────────────────────────────

_QUIZ_SYSTEM = (
    "You are a quiz master. Produce ONE multiple-choice trivia question "
    "suitable for students. Reply with STRICT minified JSON only — no prose, "
    "no markdown fences. Shape: "
    '{"question": str, "options": [str, str, str, str], '
    '"correct_index": int, "explanation": str}. '
    "Exactly 4 options. Keep question <=200 chars, each option <=80 chars, "
    "explanation <=180 chars. correct_index is 0-based into options."
)


def _user_prompt(topic: Optional[str]) -> str:
    topic = (topic or "").strip()
    if topic:
        return f"Make the question about: {topic}"
    return "Make a fun general-knowledge question."


def _extract_json(raw: str) -> Optional[dict]:
    """Best-effort parse of a model reply into a dict. Tolerates code fences
    and surrounding prose by taking the first '{' .. last '}'. None on failure."""
    if not raw:
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _validate(data: dict) -> Optional[dict]:
    """Normalize + validate a parsed quiz, truncating to Telegram's limits.
    Returns None if unusable (no question, <2 options, or a correct_index that
    doesn't point at a real option)."""
    if not isinstance(data, dict):
        return None
    question = str(data.get("question", "")).strip()[:_MAX_QUESTION]
    if not question:
        return None
    raw_options = data.get("options")
    if not isinstance(raw_options, list):
        return None
    options = []
    for o in raw_options:
        o = str(o).strip()[:_MAX_OPTION]
        if o:
            options.append(o)
    options = options[:_MAX_OPTIONS]
    if len(options) < _MIN_OPTIONS:
        return None
    # correct index — accept a few common key spellings from the model.
    idx = data.get("correct_index", data.get("answer_index", data.get("correct")))
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return None
    if not (0 <= idx < len(options)):
        return None
    explanation = str(data.get("explanation", "")).strip()[:_MAX_EXPLANATION]
    return {
        "question": question,
        "options": options,
        "correct_index": idx,
        "explanation": explanation,
    }


def generate_question(topic: Optional[str] = None) -> Optional[dict]:
    """Return a validated quiz dict {question, options, correct_index,
    explanation} or None on any failure. Never raises.

    Calls _call_main with retries=1 inside a 2-try loop: each attempt is a
    single ~25s Cerebras call, so the worst case stays ~52s — under Telegram's
    ~60s webhook window even when the first reply is unparseable.
    """
    messages = [
        {"role": "system", "content": _QUIZ_SYSTEM},
        {"role": "user", "content": _user_prompt(topic)},
    ]
    for attempt in range(2):
        try:
            raw = _call_main(messages, retries=1)
        except Exception as e:
            print(f"Quiz generation error (attempt {attempt + 1}/2): {e}")
            continue
        quiz = _validate(_extract_json(raw or "") or {})
        if quiz:
            return quiz
        print(f"Quiz parse/validate failed (attempt {attempt + 1}/2): {(raw or '')[:200]!r}")
    return None


# ── Scoring / leaderboard / streak ───────────────────────────────────────────

def _display_name(user) -> str:
    username = getattr(user, "username", None)
    if username:
        return f"@{username}"
    first = getattr(user, "first_name", None)
    return first or f"User {getattr(user, 'id', '?')}"


def save_poll(poll_id: str, chat_id: int, correct_index: int, points: int = QUIZ_POINTS) -> None:
    """Record a sent quiz poll so poll answers can be scored. No-op without a store."""
    if store is None:
        return
    try:
        store.set(
            _POLL_KEY.format(poll_id=poll_id),
            json.dumps({"chat_id": chat_id, "correct": correct_index, "points": points, "answered": []}),
            ex=QUIZ_POLL_TTL,
        )
    except Exception as e:
        print(f"Store write error (quiz save_poll): {e}")


def _bump_board(chat_id: int, user_id: int, name: str, add: int) -> int:
    """Add `add` points to a user's per-chat score, refreshing their display
    name. Returns the new score (0 without a store)."""
    if store is None:
        return 0
    key = _BOARD_KEY.format(chat_id=chat_id)
    try:
        raw = store.get(key)
        board = json.loads(raw) if raw else {}
    except Exception as e:
        print(f"Store read error (quiz board): {e}")
        board = {}
    entry = board.get(str(user_id), {"name": name, "score": 0})
    entry["name"] = name
    entry["score"] = int(entry.get("score", 0)) + add
    board[str(user_id)] = entry
    try:
        store.set(key, json.dumps(board), ex=QUIZ_BOARD_TTL)
    except Exception as e:
        print(f"Store write error (quiz board): {e}")
    return entry["score"]


def _bump_streak(user_id: int, is_correct: bool) -> int:
    """Advance a per-user daily streak on a correct answer (at most once per
    day); a gap since the last correct day resets it to 1. A wrong answer
    leaves the streak untouched — the miss surfaces as a gap next time.
    Returns the current streak_count."""
    if store is None:
        return 0
    key = _STREAK_KEY.format(user_id=user_id)
    try:
        raw = store.get(key)
        s = json.loads(raw) if raw else {}
    except Exception as e:
        print(f"Store read error (quiz streak): {e}")
        s = {}
    current = int(s.get("streak_count", 0))
    best = int(s.get("best_streak", 0))
    last = s.get("last_correct_date")
    if not is_correct:
        return current
    today = date.today()
    if last == today.isoformat():
        return current  # already counted today
    if last == (today - timedelta(days=1)).isoformat():
        current += 1
    else:
        current = 1
    best = max(best, current)
    try:
        store.set(
            key,
            json.dumps({"last_correct_date": today.isoformat(), "streak_count": current, "best_streak": best}),
            ex=QUIZ_STREAK_TTL,
        )
    except Exception as e:
        print(f"Store write error (quiz streak): {e}")
    return current


def apply_poll_answer(poll_answer) -> Optional[dict]:
    """Score one poll answer: dedupe, update the chat leaderboard + user streak.
    Returns {correct, score, streak, name} or None (no store / anonymous answer
    / retraction / unknown-or-expired poll / duplicate). Never raises."""
    if store is None:
        return None
    user = getattr(poll_answer, "user", None)
    if user is None:  # anonymous poll — can't attribute
        return None
    option_ids = getattr(poll_answer, "option_ids", None) or []
    if not option_ids:  # vote retracted
        return None
    poll_id = getattr(poll_answer, "poll_id", None)
    key = _POLL_KEY.format(poll_id=poll_id)
    try:
        raw = store.get(key)
    except Exception as e:
        print(f"Store read error (quiz poll meta): {e}")
        return None
    if raw is None:  # not one of our polls, or its scoring window expired
        return None
    try:
        meta = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if user.id in meta.get("answered", []):
        return None  # duplicate / Telegram redelivery
    meta.setdefault("answered", []).append(user.id)
    try:
        store.set(key, json.dumps(meta), ex=QUIZ_POLL_TTL)
    except Exception as e:
        print(f"Store write error (quiz poll meta): {e}")
    is_correct = option_ids[0] == meta.get("correct")
    name = _display_name(user)
    add = meta.get("points", QUIZ_POINTS) if is_correct else 0
    score = _bump_board(meta["chat_id"], user.id, name, add)
    streak = _bump_streak(user.id, is_correct)
    return {"correct": is_correct, "score": score, "streak": streak, "name": name}


def get_leaderboard(chat_id: int, top_n: int = QUIZ_LEADERBOARD_SIZE) -> Optional[list]:
    """Return [(name, score), ...] sorted high→low, capped at top_n. None when
    there's no store; [] when no scores yet."""
    if store is None:
        return None
    try:
        raw = store.get(_BOARD_KEY.format(chat_id=chat_id))
    except Exception as e:
        print(f"Store read error (quiz leaderboard): {e}")
        return []
    if not raw:
        return []
    try:
        board = json.loads(raw)
    except (ValueError, TypeError):
        return []
    rows = [(v.get("name", "?"), int(v.get("score", 0))) for v in board.values()]
    rows.sort(key=lambda r: (-r[1], r[0].lower()))
    return rows[:top_n]


# ── Subscribers (daily push) ─────────────────────────────────────────────────

def _load_subscribers() -> list:
    if store is None:
        return []
    try:
        raw = store.get(_SUBS_KEY)
        return [int(c) for c in json.loads(raw)] if raw else []
    except Exception as e:
        print(f"Store read error (quiz subscribers): {e}")
        return []


def get_subscribers() -> list:
    return _load_subscribers()


def subscribe(chat_id: int) -> str:
    """Returns 'added' | 'already' | 'no_store'."""
    if store is None:
        return "no_store"
    try:
        subs = _load_subscribers()
        if chat_id in subs:
            return "already"
        subs.append(chat_id)
        store.set(_SUBS_KEY, json.dumps(subs))  # no TTL — persists
        return "added"
    except Exception as e:
        print(f"Store write error (quiz subscribe): {e}")
        return "no_store"


def unsubscribe(chat_id: int) -> str:
    """Returns 'removed' | 'not_subscribed' | 'no_store'."""
    if store is None:
        return "no_store"
    try:
        subs = _load_subscribers()
        if chat_id not in subs:
            return "not_subscribed"
        subs = [c for c in subs if c != chat_id]
        store.set(_SUBS_KEY, json.dumps(subs))
        return "removed"
    except Exception as e:
        print(f"Store write error (quiz unsubscribe): {e}")
        return "no_store"


# ── Daily broadcast (called by /api/tick) ────────────────────────────────────

def run_daily_quiz() -> str:
    """Broadcast today's quiz to every subscriber. Returns a one-line summary
    for the /api/tick response body. Never raises.

    Idempotent per day via an atomic set_nx claim (mirrors bot/dedupe.py), so an
    overlapping scheduled+manual run or a curl --retry can't double-post. Daily
    polls are recorded via save_poll, so answers feed the SAME board/streak as
    on-demand /quiz. Per-chat send failures are counted, never fatal.
    """
    if store is None:
        return "quiz: stateless mode (no store) — skipped"

    day = date.today().isoformat()
    claim = _TICK_KEY.format(day=day)
    try:
        if not store.set_nx(claim, "1", ex=90000):
            return f"quiz: already broadcast for {day} — skipped"
    except Exception as e:
        print(f"Store error (quiz tick claim): {e}")
        return "quiz: store error on claim — skipped"

    quiz = generate_question()
    if quiz is None:
        try:
            store.delete(claim)  # release so a re-dispatch can retry today
        except Exception:
            pass
        return "quiz: generation failed — nothing sent"

    subs = get_subscribers()
    if not subs:
        return "quiz: no subscribers"

    targets = subs[:QUIZ_MAX_BROADCAST]
    skipped = len(subs) - len(targets)
    if skipped > 0:
        # House rule: log truncation loudly, never drop silently.
        print(
            f"WARNING: {skipped} subscribers over QUIZ_MAX_BROADCAST "
            f"({QUIZ_MAX_BROADCAST}) not sent for {day}: {subs[len(targets):]}"
        )

    sent = failed = 0
    for chat_id in targets:
        try:
            msg = bot.send_poll(
                chat_id,
                quiz["question"],
                quiz["options"],
                type="quiz",
                correct_option_id=quiz["correct_index"],
                explanation=quiz["explanation"] or None,
                is_anonymous=False,  # required so poll_answer reports who answered
                open_period=QUIZ_OPEN_PERIOD,
            )
            poll = getattr(msg, "poll", None)
            if poll is not None:
                save_poll(poll.id, chat_id, quiz["correct_index"])
            sent += 1
        except Exception as e:
            failed += 1
            print(f"send_poll failed for chat {chat_id}: {e}")
    return f"quiz: sent={sent} failed={failed} skipped={skipped} total_subs={len(subs)}"
