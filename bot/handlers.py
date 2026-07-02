import os
from datetime import datetime
from bot.clients import bot, BOT_INFO, store
from bot.config import COMMIT_SHA, HF_SPACE_ID, HOSTING_LABEL, MODEL, RATE_LIMIT
from bot.ai import ask_ai
from bot.helpers import is_allowed, keep_typing, send_reply, should_respond
from bot.history import clear_history
from bot.news import get_top_news, news_configured
from bot.notes import delete_note, get_note, save_note
from bot.preferences import get_provider, set_provider
from bot.rate_limit import is_rate_limited

# Verbose console logging for local dev and teaching. Enabled by
# BOT_VERBOSE_LOG=1 (run_local.py sets this automatically). Prints one
# line per inbound/outbound message so kids and teachers can see the
# conversation flow in their terminal while the bot is running.
VERBOSE_LOG = os.environ.get("BOT_VERBOSE_LOG", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def _log(message, direction: str, text: str) -> None:
    """Print a one-line trace of a message in verbose mode.

    direction is "in" (user → bot) or "out" (bot → user). Text is
    truncated to 500 characters so long AI replies don't flood the
    terminal. Newlines are collapsed for single-line readability.
    """
    if not VERBOSE_LOG:
        return
    user = message.from_user
    user_name = (
        f"@{user.username}" if user.username else (user.first_name or f"user:{user.id}")
    )
    bot_name = f"@{BOT_INFO.username}"
    snippet = (text or "").replace("\n", " ").replace("\r", " ")
    if len(snippet) > 500:
        snippet = snippet[:500] + "..."
    if direction == "in":
        sender, receiver = user_name, bot_name
    else:
        sender, receiver = bot_name, user_name
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {sender} → {receiver}: {snippet}", flush=True)


@bot.message_handler(commands=["start"], func=is_allowed)
def cmd_start(message):
    bot.send_message(
        message.chat.id,
        "Hey, I'm your AI assistant🤖 — ask me anything and I'll give you a "
        "straight, clear answer🫡:"
    )


@bot.message_handler(commands=["help"], func=is_allowed)
def cmd_help(message):
    intro = (
        "Hey, I'm your AI assistant🤖 — ask me anything and I'll give you a "
        "straight, clear answer🫡. Here's what I can do for you:"
    )
    lines = [
        intro,
        "",
        "/start — click and get conversation started",
        "/help — shows what i can do",
        "/reset — wipe our chat history and start clean",
        "/about — see what's running under the hood (model, storage, version)",
        "/compliment — get a little compliment to brighten your day",
        "/fact — get an interesting fact to make you curious",
        "/quote — get a motivational quote to inspire you",
        "/news — get the top 3 latest news in Armenia",
        "/remember <text> — save a note for later",
        "/recall — retrieve your saved note",
        "/forget — delete your saved note",
    ]
    if HF_SPACE_ID:
        lines.append("/model — switch which AI brain I'm using")
    lines.append("")
    lines.append("Just type normally otherwise — no command needed for a regular question.")
    bot.send_message(message.chat.id, "\n".join(lines))


@bot.message_handler(commands=["reset"], func=is_allowed)
def cmd_reset(message):
    clear_history(message.from_user.id)
    bot.send_message(message.chat.id, "Conversation cleared. Starting fresh!")


@bot.message_handler(commands=["about"], func=is_allowed)
def cmd_about(message):
    if HF_SPACE_ID:
        provider = get_provider(message.from_user.id)
        model_line = f"{MODEL} (main)" if provider == "main" else f"{HF_SPACE_ID} (hf)"
    else:
        model_line = MODEL
    storage_line = "SQLite" if store is not None else "stateless (no memory)"
    lines = [ 
        f"Personality: "+ ask_ai(message.from_user.id, "summarize your personality in single line"),
        f"Model  : {model_line}",
        f"Storage: {storage_line}",
        f"Hosting: {HOSTING_LABEL}",
    ]
    if COMMIT_SHA:
        lines.append(f"Version: {COMMIT_SHA}")
    bot.send_message(message.chat.id, "\n".join(lines))


if HF_SPACE_ID:

    @bot.message_handler(commands=["model"], func=is_allowed)
    def cmd_model(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            current = get_provider(message.from_user.id)
            bot.send_message(
                message.chat.id,
                f"Current provider: {current}\n\n"
                "Options:\n"
                "/model main — Cerebras (fast, multilingual, with memory)\n"
                "/model hf — ArmGPT (Armenian only, slow, no memory)",
            )
            return
        choice = parts[1].strip().lower()
        if choice not in ("main", "hf"):
            bot.send_message(
                message.chat.id, "Invalid choice. Use: /model main or /model hf"
            )
            return
        if not set_provider(message.from_user.id, choice):
            bot.send_message(
                message.chat.id, "Could not save preference. Try again later."
            )
            return
        if choice == "hf":
            bot.send_message(
                message.chat.id,
                "Switched to hf (ArmGPT).\n\n"
                "Note: this is a tiny base completion model trained only on Armenian text. "
                "It will continue whatever you write rather than answer questions, "
                "and it does not understand English. Replies take ~30-60s and there is no memory.",
            )
        else:
            bot.send_message(message.chat.id, "Switched to Main Provider.")


@bot.message_handler(content_types=["text"], func=is_allowed)
def handle_message(message):
    if not should_respond(message):
        return
    text = (message.text or "").replace(f"@{BOT_INFO.username}", "").strip()
    if not text:
        # Edited messages, forwards, or stickers-with-empty-caption can
        # arrive with no usable text. Don't burn rate-limit / AI calls on them.
        return
    _log(message, "in", text)
    if is_rate_limited(message.from_user.id):
        limit_msg = f"You've reached the daily limit of {RATE_LIMIT} messages. Try again tomorrow."
        bot.send_message(message.chat.id, limit_msg)
        _log(message, "out", f"[rate limited] {limit_msg}")
        return
    try:
        with keep_typing(message.chat.id):
            reply = ask_ai(message.from_user.id, text)
        send_reply(message, reply)
        _log(message, "out", reply)
    except Exception as e:
        print(f"Error in handle_message: {e}")
        bot.send_message(message.chat.id, "Something went wrong. Please try again.")
        _log(message, "out", f"[error] {e}")

@bot.message_handler(commands=["joke"], func=is_allowed)
def cmd_joke(message):
    reply = ask_ai(message.from_user.id, "Tell one short, clean programming joke.")
    bot.send_message(message.chat.id, reply)


@bot.message_handler(commands=["compliment"], func=is_allowed)
def cmd_compliment(message):
    name = message.from_user.first_name or "friend"
    reply = ask_ai(
        message.from_user.id,
        f"Give {name} one short, warm, genuine compliment to brighten their day. "
        "Keep it to a single friendly sentence and add a cheerful emoji.",
    )
    bot.send_message(message.chat.id, reply)



@bot.message_handler(commands=["quote"], func=is_allowed)
def cmd_quote(message):
    name = message.from_user.first_name or "friend"
    reply = ask_ai(
        message.from_user.id,
        f"Give {name} one short, genuine motivational quote to make them be motivated in every situation. "
        "Keep it to a single friendly sentence and add a masculine emoji.",
    )
    bot.send_message(message.chat.id, reply)


@bot.message_handler(commands=["fact"], func=is_allowed)
def cmd_fact(message):
    name = message.from_user.first_name or "friend"
    reply = ask_ai(
        message.from_user.id,
        f"Give {name} one interesting and true fact about that will make them curious. "
        "Keep it to a single sentence.",
    )
    bot.send_message(message.chat.id, reply)



@bot.message_handler(commands=["news"], func=is_allowed)
def cmd_news(message):
    if not news_configured():
        bot.send_message(
            message.chat.id,
            "News isn't set up for this bot yet. "
            "Add a NEWS_API_KEY (free from https://gnews.io) to enable /news.",
        )
        return
    with keep_typing(message.chat.id):
        items = get_top_news(3)
    if not items:
        bot.send_message(
            message.chat.id,
            "Couldn't fetch the news right now — please try again in a bit.",
        )
        return
    lines = ["📰 Top latest news in Armenia:", ""]
    for i, item in enumerate(items, start=1):
        lines.append(f"{i}. {item['title']}")
        detail = " — ".join(part for part in (item["source"], item["url"]) if part)
        if detail:
            lines.append(f"   {detail}")
    send_reply(message, "\n".join(lines))


@bot.message_handler(commands=["remember"], func=is_allowed)
def cmd_remember(message):
    parts = (message.text or "").split(maxsplit=1)
    note = parts[1].strip() if len(parts) > 1 else ""
    if not note:
        bot.send_message(
            message.chat.id,
            "Tell me what to remember, like: /remember buy milk tomorrow",
        )
        return
    if save_note(message.from_user.id, note):
        bot.send_message(message.chat.id, "Saved! Use /recall to get it back.")
    else:
        bot.send_message(
            message.chat.id,
            "I can't save notes right now — memory isn't set up for this bot.",
        )


@bot.message_handler(commands=["recall"], func=is_allowed)
def cmd_recall(message):
    note = get_note(message.from_user.id)
    if note:
        bot.send_message(message.chat.id, f"Here's your saved note:\n\n{note}")
    else:
        bot.send_message(
            message.chat.id,
            "You haven't saved anything yet. Use /remember <text> to save a note.",
        )


@bot.message_handler(commands=["forget"], func=is_allowed)
def cmd_forget(message):
    if get_note(message.from_user.id) is None:
        bot.send_message(message.chat.id, "There's nothing saved to forget.")
        return
    if delete_note(message.from_user.id):
        bot.send_message(message.chat.id, "Done — I've forgotten your saved note.")
    else:
        bot.send_message(
            message.chat.id,
            "I couldn't clear your note right now — memory isn't set up for this bot.",
        )


