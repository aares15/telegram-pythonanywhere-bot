"""Tests for bot.clients module-level wiring.

Focused on _init_store, which guards worker boot against a bad
SQLITE_PATH (unwritable dir, locked file, corrupt DB). A crash here
used to take the whole webhook offline; the bot must instead fall back
to stateless mode and log loudly.
"""

from unittest.mock import patch


def test_init_store_returns_none_when_path_empty():
    from bot.clients import _init_store

    assert _init_store("") is None


def test_init_store_returns_store_when_init_succeeds():
    sentinel = object()
    with patch("bot.clients.SqliteStore", return_value=sentinel) as mock_cls:
        from bot.clients import _init_store

        assert _init_store("/tmp/ok.db") is sentinel
        mock_cls.assert_called_once_with("/tmp/ok.db")


def test_init_store_falls_back_to_none_when_init_raises():
    with patch(
        "bot.clients.SqliteStore",
        side_effect=RuntimeError("disk full"),
    ):
        from bot.clients import _init_store

        assert _init_store("/tmp/bad.db") is None


def test_init_redis_returns_none_when_unconfigured():
    from bot.clients import _init_redis

    assert _init_redis("", "") is None
    assert _init_redis("https://x.upstash.io", "") is None
    assert _init_redis("", "tok") is None


def test_init_redis_returns_store_when_init_succeeds():
    sentinel = object()
    with patch("bot.clients.RedisStore", return_value=sentinel) as mock_cls:
        from bot.clients import _init_redis

        assert _init_redis("https://x.upstash.io", "tok") is sentinel
        mock_cls.assert_called_once_with("https://x.upstash.io", "tok")


def test_init_redis_falls_back_to_none_when_init_raises():
    with patch("bot.clients.RedisStore", side_effect=RuntimeError("bad token")):
        from bot.clients import _init_redis

        assert _init_redis("https://x.upstash.io", "tok") is None
