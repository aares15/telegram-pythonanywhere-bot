import os
from datetime import datetime
from bot.clients import bot, BOT_INFO, store
from bot.config import (
    COMMIT_SHA,
    HF_SPACE_ID,
    HOSTING_LABEL,
    MODEL,
    QUIZ_LEADERBOARD_SIZE,
    RATE_LIMIT,
)
from bot.ai import ask_ai
from bot.helpers import is_allowed, keep_typing, send_reply, should_respond
from bot.history import clear_history
from bot.lookup import further_reading, is_armenian_topic, wiki_lookup
from bot.news import get_top_news, get_world_news, news_configured
from bot.notes import delete_note, get_note, save_note
from bot.preferences import get_provider, set_provider
from bot.quiz import (
    apply_poll_answer,
    generate_question,
    get_leaderboard,
    save_poll,
    subscribe,
    unsubscribe,
)
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
        "Բարև! 👋 I'm your history teacher bot 📜 — a friendly guide to the "
        "past, with a special love for Armenian history (from ancient Urartu "
        "all the way to modern Armenia) and plenty to say about world history "
        "too. Ask me about a date, a person, an event — or anything else on "
        "your mind — and I'll explain it clearly. What are you curious about?"
    )


@bot.message_handler(commands=["help"], func=is_allowed)
def cmd_help(message):
    intro = (
        "Բարև! 👋 I'm your history teacher bot 📜 — ask me about Armenian "
        "history or anything from the wider past, and I'll explain it clearly "
        "and cheer on your questions. Here's what I can do for you:"
    )
    lines = [
        intro,
        "",
        "/start — click and get conversation started",
        "/help — shows what i can do",
        "/reset — wipe our chat history and start clean",
        "/about — see what's running under the hood (model, storage, version)",
        "/sha — show the live git commit SHA",
        "/lookup <topic> — look up a history topic from a real source 📚",
        "/quiz [topic] — start a trivia quiz (auto-scored) 🧠",
        "/leaderboard — see the top quiz scorers in this chat",
        "/subscribe — get a daily quiz here each morning (/unsubscribe to stop)",
        "/compliment — get a little compliment to brighten your day",
        "/fact — get an interesting fact to make you curious",
        "/quote — get a motivational quote to inspire you",
        "/newsArmenia — get the top 3 latest news in Armenia",
        "/newsWorldwide — get the top 3 interesting news around the world",
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


@bot.message_handler(commands=["sha"], func=is_allowed)
def cmd_sha(message):
    sha = COMMIT_SHA or "unknown"
    bot.send_message(message.chat.id, f"Live SHA: {sha}")


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


def _send_news(message, header, fetch):
    """Shared body for the news commands. Checks the feature is configured,
    fetches the top 3 items via `fetch`, and sends a numbered list under
    `header`. `fetch` is get_top_news (Armenia) or get_world_news (world) —
    both take a count and return a list of {title, source, url} or None."""
    if not news_configured():
        bot.send_message(
            message.chat.id,
            "News isn't set up for this bot yet. "
            "Add a NEWS_API_KEY (free from https://gnews.io) to enable news.",
        )
        return
    with keep_typing(message.chat.id):
        items = fetch(3)
    if not items:
        bot.send_message(
            message.chat.id,
            "Couldn't fetch the news right now — please try again in a bit.",
        )
        return
    lines = [header, ""]
    for i, item in enumerate(items, start=1):
        lines.append(f"{i}. {item['title']}")
        detail = " — ".join(part for part in (item["source"], item["url"]) if part)
        if detail:
            lines.append(f"   {detail}")
    send_reply(message, "\n".join(lines))


# Multiple spellings are registered per command: Telegram delivers commands
# verbatim (telebot matches them case-sensitively), so /newsArmenia and a
# lowercase/underscored /newsarmenia both need to be listed to "just work".
@bot.message_handler(
    commands=["newsArmenia", "newsarmenia", "news_armenia", "news"], func=is_allowed
)
def cmd_news(message):
    _send_news(message, "📰 Top latest news in Armenia:", get_top_news)


@bot.message_handler(
    commands=["newsWorldwide", "newsworldwide", "news_worldwide"], func=is_allowed
)
def cmd_news_worldwide(message):
    _send_news(message, "🌍 Top interesting news around the world:", get_world_news)


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


@bot.message_handler(commands=["lookup", "wiki"], func=is_allowed)
def cmd_lookup(message):
    """Look up a history topic from a real source.

    World-history topics are grounded in a live Wikipedia article and cited.
    Armenian-history topics are NEVER sourced from Wikipedia: the teacher
    answers from its own expertise and points to trusted Armenian sources.
    """
    parts = (message.text or "").split(maxsplit=1)
    topic = parts[1].strip() if len(parts) > 1 else ""
    if not topic:
        bot.send_message(
            message.chat.id,
            "Tell me what to look up, like: /lookup the Roman Empire",
        )
        return
    with keep_typing(message.chat.id):
        armenian = is_armenian_topic(topic)
        article = None
        if not armenian:
            article = wiki_lookup(topic)
            # Safety net: if the best Wikipedia hit is itself an Armenian-history
            # article, don't source it from Wikipedia — fall back to the Armenian
            # path (own expertise + trusted Armenian links).
            if article and is_armenian_topic(article["title"]):
                article, armenian = None, True

        if armenian:
            answer = ask_ai(message.from_user.id, topic)
            reply = f"{answer}\n\n{further_reading()}"
        elif article:
            context = (
                "Base your answer on this Wikipedia article extract. Stay "
                "faithful to it and do not add facts it does not support. "
                "Explain clearly for a young learner.\n\n"
                f"Article: {article['title']}\n\n{article['extract']}"
            )
            answer = ask_ai(message.from_user.id, topic, context=context)
            reply = (
                f"{answer}\n\n📖 Source: {article['title']} (Wikipedia)\n"
                f"{article['url']}"
            )
        else:
            # Couldn't reach Wikipedia — answer from the teacher's own knowledge.
            reply = ask_ai(message.from_user.id, topic)
    send_reply(message, reply)


# ── Daily Quiz Arena ─────────────────────────────────────────────────────────

@bot.message_handler(commands=["quiz", "trivia"], func=is_allowed)
def cmd_quiz(message):
    parts = (message.text or "").split(maxsplit=1)
    topic = parts[1].strip() if len(parts) > 1 else None
    with keep_typing(message.chat.id):
        quiz = generate_question(topic)
    if not quiz:
        bot.send_message(
            message.chat.id,
            "Couldn't spin up a quiz right now — give it another go in a moment.",
        )
        return
    try:
        # is_anonymous=False is REQUIRED: anonymous polls don't report who
        # answered, so scoring in group chats would be impossible otherwise.
        sent = bot.send_poll(
            message.chat.id,
            quiz["question"],
            quiz["options"],
            type="quiz",
            correct_option_id=quiz["correct_index"],
            explanation=quiz["explanation"] or None,
            is_anonymous=False,
            allows_multiple_answers=False,
        )
    except Exception as e:
        print(f"send_poll failed in /quiz: {e}")
        bot.send_message(message.chat.id, "Couldn't post the quiz — try again in a bit.")
        return
    poll = getattr(sent, "poll", None)
    if poll is not None:
        save_poll(poll.id, message.chat.id, quiz["correct_index"])


@bot.message_handler(commands=["leaderboard", "scores", "top"], func=is_allowed)
def cmd_leaderboard(message):
    board = get_leaderboard(message.chat.id, QUIZ_LEADERBOARD_SIZE)
    if board is None:
        bot.send_message(
            message.chat.id,
            "Scores need memory, which isn't set up for this bot yet.",
        )
        return
    if not board:
        bot.send_message(
            message.chat.id,
            "No scores yet — run /quiz to get the game going! 🧠",
        )
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 Quiz leaderboard:", ""]
    for i, (name, score) in enumerate(board):
        rank = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{rank} {name} — {score}")
    send_reply(message, "\n".join(lines))


@bot.message_handler(commands=["subscribe"], func=is_allowed)
def cmd_subscribe(message):
    result = subscribe(message.chat.id)
    text = {
        "added": "You're in! 🧠 You'll get the Daily Quiz here each morning. Use /unsubscribe to stop.",
        "already": "This chat is already subscribed to the Daily Quiz. ✅",
        "no_store": "The Daily Quiz needs memory, which isn't set up for this bot yet.",
    }[result]
    bot.send_message(message.chat.id, text)


@bot.message_handler(commands=["unsubscribe"], func=is_allowed)
def cmd_unsubscribe(message):
    result = unsubscribe(message.chat.id)
    text = {
        "removed": "Unsubscribed — no more daily quizzes here. 👋",
        "not_subscribed": "This chat wasn't subscribed to the Daily Quiz.",
        "no_store": "The Daily Quiz isn't set up for this bot yet.",
    }[result]
    bot.send_message(message.chat.id, text)


@bot.poll_answer_handler(func=lambda pa: True)
def on_poll_answer(poll_answer):
    # Silent scorer: the quiz poll itself already reveals correct/wrong, and a
    # per-answer reply would spam group chats. We only update the leaderboard
    # and streak. No func=is_allowed here — a PollAnswer has no from_user, so
    # is_allowed would reject every answer whenever a whitelist is set.
    try:
        apply_poll_answer(poll_answer)
    except Exception as e:
        print(f"Error in on_poll_answer: {e}")


# NOTE: handle_message MUST remain the LAST registered message handler. Its
# content_types=["text"] filter matches command messages too, and telebot runs
# the first matching handler then stops — so any command handler registered
# BELOW this one would be shadowed and never fire. Register new commands ABOVE.
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
