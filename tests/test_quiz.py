"""Tests for the Daily Quiz Arena (bot/quiz.py) + its handlers.

Uses a small in-memory FakeStore to
exercise the JSON-blob read-modify-write helpers, and MagicMock PollAnswers to
drive scoring. conftest.py mocks telebot/openai, so no keys/network are needed.
"""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import bot.quiz as quiz


class FakeStore:
    """Dict-backed stand-in for SqliteStore (ignores TTL; honors set_nx)."""

    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value

    def set_nx(self, key, value, ex=None):
        if key in self.data:
            return False
        self.data[key] = value
        return True

    def delete(self, key):
        self.data.pop(key, None)


VALID = {
    "question": "Q?",
    "options": ["a", "b", "c", "d"],
    "correct_index": 2,
    "explanation": "because",
}


def _pa(poll_id="p1", user_id=1, option_ids=(0,), username="alice", first_name="Alice"):
    pa = MagicMock()
    pa.poll_id = poll_id
    pa.user.id = user_id
    pa.user.username = username
    pa.user.first_name = first_name
    pa.option_ids = list(option_ids)
    return pa


def _seed_poll(fs, poll_id="p1", chat_id=456, correct=0, points=10):
    fs.data[quiz._POLL_KEY.format(poll_id=poll_id)] = json.dumps(
        {"chat_id": chat_id, "correct": correct, "points": points, "answered": []}
    )


# ── _extract_json ────────────────────────────────────────────────────────────


def test_extract_json_plain():
    assert quiz._extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_code_fence():
    assert quiz._extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_surrounding_prose():
    assert quiz._extract_json('Sure! {"a": 1} hope that helps') == {"a": 1}


def test_extract_json_malformed_returns_none():
    assert quiz._extract_json("not json at all") is None
    assert quiz._extract_json('{"a": ') is None
    assert quiz._extract_json("") is None


# ── _validate ────────────────────────────────────────────────────────────────


def test_validate_ok():
    assert quiz._validate(dict(VALID)) == VALID


def test_validate_truncates():
    out = quiz._validate(
        {
            "question": "x" * 400,
            "options": ["y" * 200, "b"],
            "correct_index": 0,
            "explanation": "z" * 300,
        }
    )
    assert len(out["question"]) == 300
    assert len(out["options"][0]) == 100
    assert len(out["explanation"]) == 200


def test_validate_rejects_out_of_range_index():
    assert quiz._validate({"question": "Q", "options": ["a", "b"], "correct_index": 5}) is None


def test_validate_rejects_too_few_options():
    assert quiz._validate({"question": "Q", "options": ["only"], "correct_index": 0}) is None


def test_validate_rejects_missing_question():
    assert quiz._validate({"question": "", "options": ["a", "b"], "correct_index": 0}) is None


def test_validate_rejects_missing_index():
    assert quiz._validate({"question": "Q", "options": ["a", "b"], "explanation": ""}) is None


# ── generate_question ────────────────────────────────────────────────────────


def test_generate_question_parses():
    with patch.object(quiz, "_call_main", return_value=json.dumps(VALID)):
        assert quiz.generate_question() == VALID


def test_generate_question_none_on_ai_error():
    with patch.object(quiz, "_call_main", side_effect=Exception("boom")):
        assert quiz.generate_question() is None


def test_generate_question_none_on_malformed():
    with patch.object(quiz, "_call_main", return_value="not json"):
        assert quiz.generate_question() is None


def test_generate_question_passes_topic():
    with patch.object(quiz, "_call_main", return_value=json.dumps(VALID)) as m:
        quiz.generate_question("armenian history")
        messages = m.call_args[0][0]
        assert any("armenian history" in msg["content"] for msg in messages)


# ── scoring: apply_poll_answer ───────────────────────────────────────────────


def test_apply_correct_awards_points_and_streak():
    fs = FakeStore()
    _seed_poll(fs, correct=0)
    with patch.object(quiz, "store", fs):
        res = quiz.apply_poll_answer(_pa(option_ids=[0]))
    assert res["correct"] is True
    assert res["score"] == 10
    assert res["streak"] == 1


def test_apply_wrong_no_points():
    fs = FakeStore()
    _seed_poll(fs, correct=0)
    with patch.object(quiz, "store", fs):
        res = quiz.apply_poll_answer(_pa(option_ids=[1]))
    assert res["correct"] is False
    assert res["score"] == 0


def test_apply_duplicate_ignored():
    fs = FakeStore()
    _seed_poll(fs, correct=0)
    with patch.object(quiz, "store", fs):
        quiz.apply_poll_answer(_pa(user_id=1, option_ids=[0]))
        second = quiz.apply_poll_answer(_pa(user_id=1, option_ids=[0]))
    assert second is None


def test_apply_unknown_poll_none():
    with patch.object(quiz, "store", FakeStore()):
        assert quiz.apply_poll_answer(_pa(poll_id="nope")) is None


def test_apply_retraction_none():
    fs = FakeStore()
    _seed_poll(fs)
    with patch.object(quiz, "store", fs):
        assert quiz.apply_poll_answer(_pa(option_ids=[])) is None


def test_apply_anonymous_none():
    fs = FakeStore()
    _seed_poll(fs)
    with patch.object(quiz, "store", fs):
        pa = _pa()
        pa.user = None
        assert quiz.apply_poll_answer(pa) is None


def test_apply_stateless_none():
    with patch.object(quiz, "store", None):
        assert quiz.apply_poll_answer(_pa()) is None


def test_same_user_two_chats_independent_boards_shared_streak():
    fs = FakeStore()
    _seed_poll(fs, poll_id="pA", chat_id=100, correct=0)
    _seed_poll(fs, poll_id="pB", chat_id=200, correct=0)
    with patch.object(quiz, "store", fs):
        quiz.apply_poll_answer(_pa(poll_id="pA", user_id=7, option_ids=[0]))
        r2 = quiz.apply_poll_answer(_pa(poll_id="pB", user_id=7, option_ids=[0]))
        # Independent per-chat boards: each chat records its own 10 points.
        assert quiz.get_leaderboard(100)[0][1] == 10
        assert quiz.get_leaderboard(200)[0][1] == 10
    # Shared daily streak, same day → still 1 (not double-counted).
    assert r2["streak"] == 1


def test_streak_increments_on_consecutive_day():
    fs = FakeStore()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    fs.data[quiz._STREAK_KEY.format(user_id=5)] = json.dumps(
        {"last_correct_date": yesterday, "streak_count": 4, "best_streak": 4}
    )
    _seed_poll(fs, correct=0)
    with patch.object(quiz, "store", fs):
        res = quiz.apply_poll_answer(_pa(user_id=5, option_ids=[0]))
    assert res["streak"] == 5


def test_streak_resets_after_gap():
    fs = FakeStore()
    old = (date.today() - timedelta(days=3)).isoformat()
    fs.data[quiz._STREAK_KEY.format(user_id=5)] = json.dumps(
        {"last_correct_date": old, "streak_count": 9, "best_streak": 9}
    )
    _seed_poll(fs, correct=0)
    with patch.object(quiz, "store", fs):
        res = quiz.apply_poll_answer(_pa(user_id=5, option_ids=[0]))
    assert res["streak"] == 1


# ── leaderboard ──────────────────────────────────────────────────────────────


def test_leaderboard_sorted_and_capped():
    fs = FakeStore()
    fs.data[quiz._BOARD_KEY.format(chat_id=456)] = json.dumps(
        {
            "1": {"name": "A", "score": 30},
            "2": {"name": "B", "score": 50},
            "3": {"name": "C", "score": 10},
        }
    )
    with patch.object(quiz, "store", fs):
        assert quiz.get_leaderboard(456, top_n=2) == [("B", 50), ("A", 30)]


def test_leaderboard_stateless_none():
    with patch.object(quiz, "store", None):
        assert quiz.get_leaderboard(456) is None


def test_leaderboard_empty_list():
    with patch.object(quiz, "store", FakeStore()):
        assert quiz.get_leaderboard(456) == []


# ── subscribers ──────────────────────────────────────────────────────────────


def test_subscribe_no_store():
    with patch.object(quiz, "store", None):
        assert quiz.subscribe(1) == "no_store"
        assert quiz.unsubscribe(1) == "no_store"
        assert quiz.get_subscribers() == []


def test_subscribe_add_then_already():
    with patch.object(quiz, "store", FakeStore()):
        assert quiz.subscribe(1) == "added"
        assert quiz.subscribe(1) == "already"
        assert quiz.get_subscribers() == [1]


def test_unsubscribe_removes_then_not_subscribed():
    with patch.object(quiz, "store", FakeStore()):
        quiz.subscribe(1)
        quiz.subscribe(2)
        assert quiz.unsubscribe(1) == "removed"
        assert quiz.get_subscribers() == [2]
        assert quiz.unsubscribe(1) == "not_subscribed"


# ── run_daily_quiz ───────────────────────────────────────────────────────────


def test_run_daily_quiz_stateless():
    with patch.object(quiz, "store", None):
        assert "stateless" in quiz.run_daily_quiz()


def test_run_daily_quiz_already_claimed():
    fs = FakeStore()
    fs.data[quiz._TICK_KEY.format(day=date.today().isoformat())] = "1"
    with (
        patch.object(quiz, "store", fs),
        patch.object(quiz, "generate_question") as gen,
    ):
        msg = quiz.run_daily_quiz()
    assert "already broadcast" in msg
    gen.assert_not_called()


def test_run_daily_quiz_happy_path():
    fs = FakeStore()
    fs.data[quiz._SUBS_KEY] = json.dumps([11, 22, 33])
    mock_bot = MagicMock()
    mock_bot.send_poll.return_value = MagicMock(poll=MagicMock(id="pp"))
    with (
        patch.object(quiz, "store", fs),
        patch.object(quiz, "generate_question", return_value=dict(VALID)),
        patch.object(quiz, "bot", mock_bot),
    ):
        msg = quiz.run_daily_quiz()
    assert "sent=3" in msg and "failed=0" in msg
    assert mock_bot.send_poll.call_count == 3


def test_run_daily_quiz_one_send_fails():
    fs = FakeStore()
    fs.data[quiz._SUBS_KEY] = json.dumps([11, 22, 33])
    mock_bot = MagicMock()
    mock_bot.send_poll.side_effect = [
        MagicMock(poll=MagicMock(id="p1")),
        Exception("bot was blocked"),
        MagicMock(poll=MagicMock(id="p3")),
    ]
    with (
        patch.object(quiz, "store", fs),
        patch.object(quiz, "generate_question", return_value=dict(VALID)),
        patch.object(quiz, "bot", mock_bot),
    ):
        msg = quiz.run_daily_quiz()
    assert "sent=2" in msg and "failed=1" in msg


def test_run_daily_quiz_over_cap_logs_skip():
    fs = FakeStore()
    fs.data[quiz._SUBS_KEY] = json.dumps(list(range(1, 6)))  # 5 subscribers
    mock_bot = MagicMock()
    mock_bot.send_poll.return_value = MagicMock(poll=MagicMock(id="pp"))
    with (
        patch.object(quiz, "store", fs),
        patch.object(quiz, "generate_question", return_value=dict(VALID)),
        patch.object(quiz, "bot", mock_bot),
        patch.object(quiz, "QUIZ_MAX_BROADCAST", 2),
    ):
        msg = quiz.run_daily_quiz()
    assert "sent=2" in msg and "skipped=3" in msg


def test_run_daily_quiz_generation_failure_releases_claim():
    fs = FakeStore()
    fs.data[quiz._SUBS_KEY] = json.dumps([11])
    claim = quiz._TICK_KEY.format(day=date.today().isoformat())
    with (
        patch.object(quiz, "store", fs),
        patch.object(quiz, "generate_question", return_value=None),
    ):
        msg = quiz.run_daily_quiz()
    assert "generation failed" in msg
    assert claim not in fs.data  # claim released so a re-dispatch can retry


# ── personalized quiz (transcript, build, session flow) ──────────────────────

_HISTORY = [
    {"role": "user", "content": "who was Tigran the Great?"},
    {"role": "assistant", "content": "Tigran II ruled Armenia at its height..."},
]

_SIX = [
    {"question": f"Q{i}?", "options": ["a", "b", "c", "d"], "correct_index": 0, "explanation": "e"}
    for i in range(6)
]


def _poll_bot(prefix="sp"):
    """Mock bot whose send_poll returns a unique poll id each call (sp0, sp1, …),
    mirroring Telegram — each poll really has a distinct id."""
    mb = MagicMock()
    counter = {"n": 0}

    def _send(*args, **kwargs):
        i = counter["n"]
        counter["n"] += 1
        return MagicMock(poll=MagicMock(id=f"{prefix}{i}"))

    mb.send_poll.side_effect = _send
    return mb


def test_transcript_labels_roles():
    t = quiz._transcript_from_history(_HISTORY)
    assert "Student: who was Tigran the Great?" in t
    assert "Teacher: Tigran II ruled" in t


def test_transcript_empty_without_content():
    assert quiz._transcript_from_history([{"role": "user", "content": "  "}]) == ""


def test_extract_json_array_plain():
    assert quiz._extract_json_array('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]


def test_extract_json_array_wrapper():
    assert quiz._extract_json_array('{"questions": [{"a": 1}]}') == [{"a": 1}]


def test_extract_json_array_none():
    assert quiz._extract_json_array("not an array") is None


def test_build_personal_quiz_parses():
    with patch.object(quiz, "_call_main", return_value=json.dumps(_SIX)):
        out = quiz.build_personal_quiz(_HISTORY, n=6)
    assert out is not None
    assert len(out) == 6
    assert out[0]["question"] == "Q0?"


def test_build_personal_quiz_empty_history_skips_ai():
    with patch.object(quiz, "_call_main") as call:
        assert quiz.build_personal_quiz([]) is None
        call.assert_not_called()


def test_build_personal_quiz_none_on_error():
    with patch.object(quiz, "_call_main", side_effect=RuntimeError("boom")):
        assert quiz.build_personal_quiz(_HISTORY) is None


def test_start_session_and_send_first_question():
    fs = FakeStore()
    mb = _poll_bot()
    with patch.object(quiz, "store", fs), patch.object(quiz, "bot", mb):
        assert quiz.start_session(456, 1, _SIX) is True
        assert quiz.send_next_question(456, 1) is True
    assert fs.data[quiz._SESSION_KEY.format(chat_id=456, user_id=1)]
    spoll = json.loads(fs.data[quiz._SPOLL_KEY.format(poll_id="sp0")])
    assert spoll["q_index"] == 0 and spoll["user_id"] == 1
    assert mb.send_poll.call_args[0][1].startswith("Q1/6:")


def test_apply_session_answer_scores_and_advances():
    fs = FakeStore()
    mb = _poll_bot()
    with patch.object(quiz, "store", fs), patch.object(quiz, "bot", mb):
        quiz.start_session(456, 1, _SIX)
        quiz.send_next_question(456, 1)  # Q1 → poll "sp0"
        handled = quiz.apply_session_answer(_pa(poll_id="sp0", user_id=1, option_ids=[0]))
    assert handled is True
    session = json.loads(fs.data[quiz._SESSION_KEY.format(chat_id=456, user_id=1)])
    assert session["index"] == 1
    assert session["score"] == 1  # correct_index 0 chosen
    assert mb.send_poll.call_count == 2  # next question auto-sent


def test_apply_session_answer_unknown_poll_returns_false():
    with patch.object(quiz, "store", FakeStore()):
        assert quiz.apply_session_answer(_pa(poll_id="ghost", user_id=1)) is False


def test_apply_session_answer_duplicate_not_double_counted():
    fs = FakeStore()
    mb = _poll_bot()
    with patch.object(quiz, "store", fs), patch.object(quiz, "bot", mb):
        quiz.start_session(456, 1, _SIX)
        quiz.send_next_question(456, 1)
        quiz.apply_session_answer(_pa(poll_id="sp0", user_id=1, option_ids=[0]))
        again = quiz.apply_session_answer(_pa(poll_id="sp0", user_id=1, option_ids=[0]))
    assert again is True  # handled (swallowed)
    session = json.loads(fs.data[quiz._SESSION_KEY.format(chat_id=456, user_id=1)])
    assert session["index"] == 1 and session["score"] == 1


def test_apply_session_answer_final_reports_score():
    fs = FakeStore()
    mb = _poll_bot()
    with patch.object(quiz, "store", fs), patch.object(quiz, "bot", mb):
        quiz.start_session(456, 1, [dict(VALID)])  # single-question quiz
        quiz.send_next_question(456, 1)  # poll "sp0", correct_index 2
        quiz.apply_session_answer(_pa(poll_id="sp0", user_id=1, option_ids=[2]))
    assert quiz._SESSION_KEY.format(chat_id=456, user_id=1) not in fs.data
    assert "1/1" in mb.send_message.call_args[0][1]


def test_apply_session_answer_ignores_other_user():
    fs = FakeStore()
    mb = _poll_bot()
    with patch.object(quiz, "store", fs), patch.object(quiz, "bot", mb):
        quiz.start_session(456, 1, _SIX)
        quiz.send_next_question(456, 1)
        handled = quiz.apply_session_answer(_pa(poll_id="sp0", user_id=999, option_ids=[0]))
    assert handled is True  # it's a session poll, but not this user's — swallowed
    session = json.loads(fs.data[quiz._SESSION_KEY.format(chat_id=456, user_id=1)])
    assert session["index"] == 0  # not advanced by the wrong user


# ── handlers ─────────────────────────────────────────────────────────────────


def test_cmd_quiz_needs_store():
    from tests.test_handlers import make_message

    with (
        patch("bot.handlers.store", None),
        patch("bot.handlers.build_personal_quiz") as gen,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_quiz

        cmd_quiz(make_message(text="/quiz"))
        gen.assert_not_called()
        assert "memory" in mock_bot.send_message.call_args[0][1]


def test_cmd_quiz_needs_conversation_first():
    from tests.test_handlers import make_message

    with (
        patch("bot.handlers.store", MagicMock()),
        patch("bot.handlers.get_history", return_value=[]),
        patch("bot.handlers.build_personal_quiz") as gen,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_quiz

        cmd_quiz(make_message(text="/quiz"))
        gen.assert_not_called()
        assert "chat first" in mock_bot.send_message.call_args[0][1].lower()


def test_cmd_quiz_starts_session_and_sends_first():
    from tests.test_handlers import make_message

    history = [
        {"role": "user", "content": "tell me about Urartu"},
        {"role": "assistant", "content": "Urartu was an ancient kingdom..."},
    ]
    with (
        patch("bot.handlers.store", MagicMock()),
        patch("bot.handlers.get_history", return_value=history),
        patch("bot.handlers.keep_typing"),
        patch("bot.handlers.build_personal_quiz", return_value=_SIX) as gen,
        patch("bot.handlers.start_session", return_value=True) as start,
        patch("bot.handlers.send_next_question", return_value=True) as nxt,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import cmd_quiz

        cmd_quiz(make_message(text="/quiz"))
        gen.assert_called_once_with(history)
        start.assert_called_once()
        nxt.assert_called_once()


def test_cmd_quiz_generation_failure():
    from tests.test_handlers import make_message

    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello there"},
    ]
    with (
        patch("bot.handlers.store", MagicMock()),
        patch("bot.handlers.get_history", return_value=history),
        patch("bot.handlers.keep_typing"),
        patch("bot.handlers.build_personal_quiz", return_value=None),
        patch("bot.handlers.start_session") as start,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_quiz

        cmd_quiz(make_message(text="/quiz"))
        start.assert_not_called()
        assert "Couldn't" in mock_bot.send_message.call_args[0][1]


def test_cmd_leaderboard_stateless():
    from tests.test_handlers import make_message

    with (
        patch("bot.handlers.get_leaderboard", return_value=None),
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_leaderboard

        cmd_leaderboard(make_message())
        assert "isn't set up" in mock_bot.send_message.call_args[0][1]


def test_cmd_leaderboard_empty():
    from tests.test_handlers import make_message

    with (
        patch("bot.handlers.get_leaderboard", return_value=[]),
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_leaderboard

        cmd_leaderboard(make_message())
        assert "No scores yet" in mock_bot.send_message.call_args[0][1]


def test_cmd_leaderboard_renders():
    from tests.test_handlers import make_message

    with (
        patch("bot.handlers.get_leaderboard", return_value=[("@alice", 30), ("@bob", 10)]),
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import cmd_leaderboard

        cmd_leaderboard(make_message())
        sent = mock_send.call_args[0][1]
        assert "@alice" in sent and "30" in sent


def test_cmd_subscribe_added():
    from tests.test_handlers import make_message

    with (
        patch("bot.handlers.subscribe", return_value="added"),
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_subscribe

        cmd_subscribe(make_message())
        assert "Daily Quiz" in mock_bot.send_message.call_args[0][1]


def test_cmd_unsubscribe_removed():
    from tests.test_handlers import make_message

    with (
        patch("bot.handlers.unsubscribe", return_value="removed"),
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_unsubscribe

        cmd_unsubscribe(make_message())
        assert "Unsubscribed" in mock_bot.send_message.call_args[0][1]


def test_on_poll_answer_delegates_to_legacy_when_not_a_session():
    with (
        patch("bot.handlers.apply_session_answer", return_value=False),
        patch("bot.handlers.apply_poll_answer") as mock_apply,
    ):
        from bot.handlers import on_poll_answer

        on_poll_answer(_pa())
        mock_apply.assert_called_once()


def test_on_poll_answer_prefers_session():
    with (
        patch("bot.handlers.apply_session_answer", return_value=True) as sess,
        patch("bot.handlers.apply_poll_answer") as legacy,
    ):
        from bot.handlers import on_poll_answer

        on_poll_answer(_pa())
        sess.assert_called_once()
        legacy.assert_not_called()


def test_personal_quiz_end_to_end():
    """Drive the whole feature through the REAL engine: cmd_quiz builds a quiz
    from history, asks 6 questions one at a time, and reports the final score."""
    from tests.test_handlers import make_message
    import bot.handlers as handlers
    import bot.history as history

    fs = FakeStore()
    fs.data["chat:123"] = json.dumps(
        [
            {"role": "user", "content": "who was Tigran the Great?"},
            {"role": "assistant", "content": "Tigran II expanded Armenia to its height..."},
        ]
    )
    mb = _poll_bot()  # send_poll → sp0, sp1, …; send_message captured

    with (
        patch.object(quiz, "store", fs),
        patch.object(quiz, "bot", mb),
        patch.object(quiz, "_call_main", return_value=json.dumps(_SIX)),
        patch.object(history, "store", fs),
        patch.object(handlers, "store", fs),
        patch.object(handlers, "bot", mb),
        patch("bot.handlers.keep_typing"),
    ):
        handlers.cmd_quiz(make_message(text="/quiz", user_id=123, chat_id=456))
        # All six answered correctly (every _SIX question has correct_index 0).
        for i in range(6):
            handlers.on_poll_answer(_pa(poll_id=f"sp{i}", user_id=123, option_ids=[0]))

    # Six polls sent, session cleared, and the final score reported.
    assert mb.send_poll.call_count == 6
    assert quiz._SESSION_KEY.format(chat_id=456, user_id=123) not in fs.data
    assert "6/6" in mb.send_message.call_args[0][1]
