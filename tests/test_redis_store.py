"""Tests for RedisStore — the Upstash REST backend that gives the bot memory
on serverless hosts (Vercel) where SqliteStore can't persist.

requests is mocked globally in conftest.py, so we patch bot.store.requests.post
to script Upstash REST replies ({"result": ...} / {"error": ...})."""

from unittest.mock import MagicMock, patch

import pytest


def _resp(payload):
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = payload
    return r


def _make_store(post_mock):
    """Build a RedisStore, satisfying the __init__ PING with a PONG first."""
    from bot.store import RedisStore

    post_mock.return_value = _resp({"result": "PONG"})
    store = RedisStore("https://example.upstash.io", "tok")
    post_mock.reset_mock()
    return store


def test_init_pings_and_raises_on_bad_pong():
    from bot.store import RedisStore

    with patch("bot.store.requests.post", return_value=_resp({"result": "nope"})):
        with pytest.raises(RuntimeError):
            RedisStore("https://example.upstash.io", "tok")


def test_init_succeeds_on_pong():
    with patch("bot.store.requests.post") as post:
        store = _make_store(post)
        assert store is not None


def test_get_returns_value_and_none():
    with patch("bot.store.requests.post") as post:
        store = _make_store(post)
        post.return_value = _resp({"result": "hello"})
        assert store.get("k") == "hello"
        post.return_value = _resp({"result": None})
        assert store.get("missing") is None


def test_set_with_and_without_ttl():
    with patch("bot.store.requests.post") as post:
        store = _make_store(post)
        post.return_value = _resp({"result": "OK"})
        store.set("k", "v")
        assert post.call_args.kwargs["json"] == ["SET", "k", "v"]
        store.set("k", "v", ex=60)
        assert post.call_args.kwargs["json"] == ["SET", "k", "v", "EX", "60"]


def test_set_nx_true_on_ok_false_on_null():
    with patch("bot.store.requests.post") as post:
        store = _make_store(post)
        post.return_value = _resp({"result": "OK"})
        assert store.set_nx("k", "v", ex=30) is True
        assert post.call_args.kwargs["json"] == ["SET", "k", "v", "EX", "30", "NX"]
        post.return_value = _resp({"result": None})
        assert store.set_nx("k", "v") is False


def test_incr_returns_int():
    with patch("bot.store.requests.post") as post:
        store = _make_store(post)
        post.return_value = _resp({"result": 5})
        assert store.incr("count") == 5
        assert post.call_args.kwargs["json"] == ["INCR", "count"]


def test_delete_and_expire_commands():
    with patch("bot.store.requests.post") as post:
        store = _make_store(post)
        post.return_value = _resp({"result": 1})
        store.delete("k")
        assert post.call_args.kwargs["json"] == ["DEL", "k"]
        store.expire("k", 120)
        assert post.call_args.kwargs["json"] == ["EXPIRE", "k", "120"]


def test_cmd_raises_on_error_payload():
    with patch("bot.store.requests.post") as post:
        store = _make_store(post)
        post.return_value = _resp({"error": "WRONGTYPE"})
        with pytest.raises(RuntimeError):
            store.get("k")


def test_auth_header_is_bearer_token():
    with patch("bot.store.requests.post") as post:
        _make_store(post)
        # The PING call carried the Authorization header
        post.return_value = _resp({"result": "PONG"})
        from bot.store import RedisStore

        RedisStore("https://example.upstash.io", "secret-tok")
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer secret-tok"
