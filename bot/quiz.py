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
    QUIZ_PERSONAL_COUNT,
    QUIZ_POINTS,
    QUIZ_POLL_TTL,
    QUIZ_SESSION_TTL,
    QUIZ_STREAK_TTL,
)
from bot.providers import _call_main

# ── Storage keys ─────────────────────────────────────────────────────────────
_POLL_KEY = "quiz:poll:{poll_id}"       # {chat_id, correct, points, answered:[uid]}
_BOARD_KEY = "quiz:board:{chat_id}"     # {"<uid>": {name, score}}
_STREAK_KEY = "quiz:streak:{user_id}"   # {last_correct_date, streak_count, best_streak}
_SUBS_KEY = "quiz:subscribers"          # [chat_id, ...]
_TICK_KEY = "quiz:tick:{day}"           # "1" — once-per-day broadcast claim
# Personalized quiz (per-user session drawn from their own conversation):
_SESSION_KEY = "quiz:session:{chat_id}:{user_id}"  # {questions:[...], index, score}
_SPOLL_KEY = "quiz:spoll:{poll_id}"                # {chat_id, user_id, q_index, correct}

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


# ── Personalized quiz (from the user's own conversation) ─────────────────────

_PERSONAL_SYSTEM = (
    "You are a quiz master creating a short review quiz for a student, based "
    "ONLY on the conversation transcript the user provides (a chat between the "
    "student and their history teacher). Write multiple-choice questions that "
    "test what the STUDENT learned from the teacher's explanations. Base every "
    "question and its options strictly on facts stated in the transcript — do "
    "not add outside facts. Reply with STRICT minified JSON only (no prose, no "
    "markdown fences): a JSON ARRAY of objects, each "
    '{"question": str, "options": [str, str, str, str], '
    '"correct_index": int, "explanation": str}. Exactly 4 options each. '
    "question <=200 chars, each option <=80 chars, explanation <=180 chars. "
    "correct_index is 0-based into options."
)

# Bound the transcript fed to the model: cap each message and the whole blob so
# a long history can't blow the prompt size / latency budget.
_MSG_CAP = 800
_TRANSCRIPT_CAP = 6000


def _transcript_from_history(history: list) -> str:
    """Render conversation history into a 'Student:/Teacher:' transcript (most
    recent kept when it exceeds the cap). Empty string if nothing usable."""
    lines = []
    for m in history or []:
        content = str(m.get("content", "")).strip()
        if not content:
            continue
        who = "Student" if m.get("role") == "user" else "Teacher"
        lines.append(f"{who}: {content[:_MSG_CAP]}")
    return "\n".join(lines)[-_TRANSCRIPT_CAP:]


def _extract_json_array(raw: str) -> Optional[list]:
    """Parse a JSON array of questions from a model reply. Tolerates fences /
    prose (first '[' .. last ']') and a {"questions": [...]} wrapper."""
    if not raw:
        return None
    start, end = raw.find("["), raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            if isinstance(data, list):
                return data
        except (ValueError, TypeError):
            pass
    obj = _extract_json(raw)
    if isinstance(obj, dict) and isinstance(obj.get("questions"), list):
        return obj["questions"]
    return None


def build_personal_quiz(history: list, n: int = QUIZ_PERSONAL_COUNT) -> Optional[list]:
    """Generate up to `n` validated questions grounded in the user's own
    conversation. Returns a list of quiz dicts, or None on any failure. Never
    raises. Two attempts, each a single retrying _call_main, to stay under
    Telegram's ~60s webhook window."""
    transcript = _transcript_from_history(history)
    if not transcript:
        return None
    messages = [
        {"role": "system", "content": _PERSONAL_SYSTEM},
        {"role": "user", "content": f"Transcript:\n{transcript}\n\nWrite exactly {n} questions now as a JSON array."},
    ]
    for attempt in range(2):
        try:
            raw = _call_main(messages, retries=1)
        except Exception as e:
            print(f"Personal quiz generation error (attempt {attempt + 1}/2): {e}")
            continue
        items = _extract_json_array(raw or "")
        if items:
            questions = [q for q in (_validate(it) for it in items) if q]
            if questions:
                return questions[:n]
        print(f"Personal quiz parse failed (attempt {attempt + 1}/2): {(raw or '')[:200]!r}")
    return None


def _session_key(chat_id, user_id) -> str:
    return _SESSION_KEY.format(chat_id=chat_id, user_id=user_id)


def _load_session(chat_id, user_id) -> Optional[dict]:
    if store is None:
        return None
    try:
        raw = store.get(_session_key(chat_id, user_id))
        return json.loads(raw) if raw else None
    except Exception as e:
        print(f"Store read error (quiz session): {e}")
        return None


def _save_session(chat_id, user_id, session: dict) -> None:
    if store is None:
        return
    try:
        store.set(_session_key(chat_id, user_id), json.dumps(session), ex=QUIZ_SESSION_TTL)
    except Exception as e:
        print(f"Store write error (quiz session): {e}")


def _delete_session(chat_id, user_id) -> None:
    if store is None:
        return
    try:
        store.delete(_session_key(chat_id, user_id))
    except Exception as e:
        print(f"Store delete error (quiz session): {e}")


def start_session(chat_id: int, user_id: int, questions: list) -> bool:
    """Begin a personalized quiz: store the questions + zeroed progress. Returns
    False (no store / write failed) so the caller can bail gracefully."""
    if store is None or not questions:
        return False
    _save_session(chat_id, user_id, {"questions": questions, "index": 0, "score": 0})
    return _load_session(chat_id, user_id) is not None


def send_next_question(chat_id: int, user_id: int) -> bool:
    """Send the session's current question as a quiz poll and map the poll back
    to the session so the answer can advance it. Returns False if there's no
    active session, it's already finished, or the send failed."""
    session = _load_session(chat_id, user_id)
    if not session:
        return False
    questions = session.get("questions", [])
    idx = session.get("index", 0)
    if idx >= len(questions):
        return False
    q = questions[idx]
    prompt = f"Q{idx + 1}/{len(questions)}: {q['question']}"[:_MAX_QUESTION]
    try:
        msg = bot.send_poll(
            chat_id,
            prompt,
            q["options"],
            type="quiz",
            correct_option_id=q["correct_index"],
            explanation=q["explanation"] or None,
            is_anonymous=False,  # required so poll_answer reports who answered
            allows_multiple_answers=False,
        )
    except Exception as e:
        print(f"send_poll failed (personal quiz): {e}")
        return False
    poll = getattr(msg, "poll", None)
    if poll is None:
        return False
    if store is not None:
        try:
            store.set(
                _SPOLL_KEY.format(poll_id=poll.id),
                json.dumps({"chat_id": chat_id, "user_id": user_id, "q_index": idx, "correct": q["correct_index"]}),
                ex=QUIZ_SESSION_TTL,
            )
        except Exception as e:
            print(f"Store write error (quiz spoll): {e}")
            return False
    return True


def _finish_session(chat_id: int, user_id: int, score: int, total: int) -> None:
    """Clear the session and send the final score with a bit of encouragement."""
    _delete_session(chat_id, user_id)
    pct = (score / total) if total else 0
    if pct >= 1:
        note = "Perfect score! 🏆 You really know your history."
    elif pct >= 0.7:
        note = "Great job! 🎉"
    elif pct >= 0.4:
        note = "Nice effort — review the ones you missed and try again. 👍"
    else:
        note = "Keep studying — ask me more and you'll ace the next one. 📚"
    try:
        bot.send_message(chat_id, f"🏁 Quiz complete! You scored {score}/{total}.\n{note}")
    except Exception as e:
        print(f"send_message failed (quiz result): {e}")


def apply_session_answer(poll_answer) -> bool:
    """Handle a poll answer belonging to a personalized-quiz session: score it,
    advance the session, and send the next question or the final result.

    Returns True if the answer belonged to a session (handled — the caller must
    NOT fall through to the leaderboard scorer), False otherwise. Never raises.
    """
    if store is None:
        return False
    poll_id = getattr(poll_answer, "poll_id", None)
    if not poll_id:
        return False
    try:
        raw = store.get(_SPOLL_KEY.format(poll_id=poll_id))
    except Exception as e:
        print(f"Store read error (quiz spoll): {e}")
        return False
    if not raw:
        return False  # not a personalized-quiz poll → caller uses legacy scorer
    try:
        meta = json.loads(raw)
    except (ValueError, TypeError):
        return True  # it was ours but is corrupt — swallow, don't double-handle
    user = getattr(poll_answer, "user", None)
    if user is None or user.id != meta.get("user_id"):
        return True  # someone else answered this user's quiz poll — ignore
    option_ids = getattr(poll_answer, "option_ids", None) or []
    if not option_ids:
        return True  # retraction (quiz polls can't really retract) — ignore
    session = _load_session(meta["chat_id"], meta["user_id"])
    if not session:
        return True  # session expired / cleared
    if meta.get("q_index") != session.get("index"):
        return True  # duplicate redelivery or stale answer — already advanced
    if option_ids[0] == meta.get("correct"):
        session["score"] = int(session.get("score", 0)) + 1
    session["index"] = int(session.get("index", 0)) + 1
    total = len(session.get("questions", []))
    if session["index"] < total:
        _save_session(meta["chat_id"], meta["user_id"], session)
        send_next_question(meta["chat_id"], meta["user_id"])
    else:
        _finish_session(meta["chat_id"], meta["user_id"], session["score"], total)
    return True


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
