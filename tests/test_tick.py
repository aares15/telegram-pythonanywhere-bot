"""Tests for the /api/tick daily-quiz broadcast endpoint.

Mirrors tests/test_deploy.py's security tests: secret verification and
fail-closed behavior. /api/tick only broadcasts a quiz, but it must still
reject unauthenticated callers so nobody can spam subscribers — and it must
authenticate with its OWN secret/header, distinct from the webhook and deploy
secrets.
"""

from unittest.mock import MagicMock, patch


def test_tick_fails_closed_when_secret_unset():
    """If TICK_SECRET is empty, /api/tick MUST refuse all requests."""
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "anything"
    with (
        patch("bot.config.TICK_SECRET", ""),
        patch("api.index.request", mock_request),
    ):
        from api.index import tick

        body, status = tick()
        assert status == 403


def test_tick_rejects_bad_secret():
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "wrong"
    with (
        patch("bot.config.TICK_SECRET", "correct"),
        patch("api.index.request", mock_request),
    ):
        from api.index import tick

        body, status = tick()
        assert status == 403


def test_tick_uses_compare_digest():
    """Constant-time comparison guards against timing-attack secret recovery."""
    import inspect

    from api import index

    assert "hmac.compare_digest" in inspect.getsource(index.tick)


def test_tick_reads_tick_header_not_webhook_header():
    """Must authenticate with X-Tick-Secret — never the Telegram webhook
    header (a different secret)."""
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "correct"
    with (
        patch("bot.config.TICK_SECRET", "correct"),
        patch("api.index.request", mock_request),
        patch("bot.quiz.run_daily_quiz", return_value="quiz: no subscribers"),
    ):
        from api.index import tick

        tick()
        mock_request.headers.get.assert_called_with("X-Tick-Secret", "")


def test_tick_valid_secret_runs_broadcast():
    mock_request = MagicMock()
    mock_request.headers.get.return_value = "correct"
    with (
        patch("bot.config.TICK_SECRET", "correct"),
        patch("api.index.request", mock_request),
        patch(
            "bot.quiz.run_daily_quiz",
            return_value="quiz: sent=3 failed=0 skipped=0 total_subs=3",
        ) as mock_run,
    ):
        from api.index import tick

        body, status = tick()
        assert status == 200
        assert "sent=3" in body
        mock_run.assert_called_once()
