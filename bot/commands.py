"""Single source of truth for the bot's command list.

Both the /help text (bot/handlers.py) and the Telegram "/" autocomplete menu
(bot/clients.py::register_commands, via set_my_commands) are built from
command_specs() so they can never drift apart. When you add or remove a command
handler, edit COMMANDS here — the /help output AND the "/" menu both follow.

Why this matters: the "/" menu lives on Telegram's servers. It was set once
(via BotFather or a set_my_commands call) and does NOT update just because you
change the code. Deleting a handler leaves its stale entry in the menu until
set_my_commands is called again — which is exactly what register_commands()
does at every boot / deploy.

Each entry is (command, menu_description, help_line):
  - command          — the /name Telegram registers (lowercase, no slash)
  - menu_description  — short text shown next to the command in the "/" menu
  - help_line         — the friendlier line shown by /help and /start
"""

from bot import config

# Always-registered commands, in the order they should appear.
COMMANDS = [
    ("start", "Start the conversation", "/start — click and get conversation started"),
    ("help", "Show what I can do", "/help — shows what i can do"),
    ("reset", "Wipe our chat history and start clean", "/reset — wipe our chat history and start clean"),
    ("about", "Model, storage, and version info", "/about — see what's running under the hood (model, storage, version)"),
    ("lookup", "Look up a history topic from a real source", "/lookup <topic> — look up a history topic from a real source 📚"),
    ("quiz", "Quiz yourself on what you've learned", "/quiz — take a quiz on what we've covered, scored at the end 🧠"),
    ("leaderboard", "See the top quiz scorers in this chat", "/leaderboard — see the top quiz scorers in this chat"),
    ("subscribe", "Get a daily quiz here each morning", "/subscribe — get a daily quiz here each morning (/unsubscribe to stop)"),
    ("fact", "Get an interesting history fact", "/fact — get an interesting fact to make you curious"),
    ("remember", "Save a note for later", "/remember <text> — save a note for later"),
    ("recall", "Retrieve your saved note", "/recall — retrieve your saved note"),
    ("forget", "Delete your saved note", "/forget — delete your saved note"),
]

# Only registered when a Hugging Face space is configured (HF_SPACE_ID). The
# /model handler itself is guarded by the same condition in bot/handlers.py.
_MODEL_COMMAND = ("model", "Switch which AI brain I'm using", "/model — switch which AI brain I'm using")


def command_specs():
    """Return the active command specs, including /model only when HF is set.

    Reads config.HF_SPACE_ID at call time so the list reflects the current
    configuration (and so tests can patch it)."""
    specs = list(COMMANDS)
    if config.HF_SPACE_ID:
        specs.append(_MODEL_COMMAND)
    return specs
