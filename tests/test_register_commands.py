"""Tests for the "/" command-menu registration (bot.clients.register_commands)
and its source of truth (bot.commands.command_specs).

register_commands runs on the first webhook request after every cold start
(api/index.py::_bootstrap_once) — like register_webhook, failures must never
bubble up and crash the request. This is what keeps Telegram's "/" menu in sync
with the actual command handlers.
"""

from unittest.mock import patch


# ── command_specs (source of truth) ───────────────────────────────────────────


def test_command_specs_includes_core_commands():
    from bot.commands import command_specs

    names = [name for (name, _desc, _help) in command_specs()]
    for expected in ("start", "help", "reset", "about", "lookup", "quiz", "fact"):
        assert expected in names


def test_command_specs_excludes_removed_commands():
    """Commands deleted this session must never resurface in the menu/help."""
    from bot.commands import command_specs

    names = [name for (name, _desc, _help) in command_specs()]
    for removed in ("news", "newsarmenia", "newsworldwide", "joke", "sha", "compliment", "quote"):
        assert removed not in names


def test_command_specs_omits_model_without_hf():
    with patch("bot.config.HF_SPACE_ID", ""):
        from bot.commands import command_specs

        names = [name for (name, _desc, _help) in command_specs()]
        assert "model" not in names


def test_command_specs_includes_model_with_hf():
    with patch("bot.config.HF_SPACE_ID", "some/space"):
        from bot.commands import command_specs

        names = [name for (name, _desc, _help) in command_specs()]
        assert "model" in names


# ── register_commands ─────────────────────────────────────────────────────────


def test_register_commands_calls_set_my_commands():
    with patch("bot.clients.bot") as mock_bot:
        from bot.clients import register_commands
        from bot.commands import command_specs

        msg = register_commands()
        mock_bot.set_my_commands.assert_called_once()
        # One BotCommand per active spec.
        passed = mock_bot.set_my_commands.call_args[0][0]
        assert len(passed) == len(command_specs())
        assert "Registered" in msg


def test_register_commands_clears_narrower_scopes():
    """After setting the default scope, the all-private-chats / all-group-chats
    scopes are deleted so a stale per-scope menu can't shadow the new list."""
    with patch("bot.clients.bot") as mock_bot:
        from bot.clients import register_commands

        register_commands()
        assert mock_bot.delete_my_commands.call_count == 2


def test_register_commands_does_not_raise_on_failure():
    """Failures are retried, then logged and swallowed — never raised."""
    with (
        patch("bot.clients.bot") as mock_bot,
        patch("bot.clients.time.sleep") as mock_sleep,
    ):
        mock_bot.set_my_commands.side_effect = RuntimeError("Telegram down")
        from bot.clients import register_commands

        msg = register_commands()
        assert "fail" in msg.lower()
        assert mock_bot.set_my_commands.call_count == 3
        assert mock_sleep.call_count == 2


def test_register_commands_reports_false_return():
    with patch("bot.clients.bot") as mock_bot:
        mock_bot.set_my_commands.return_value = False
        from bot.clients import register_commands

        msg = register_commands()
        assert "False" in msg
