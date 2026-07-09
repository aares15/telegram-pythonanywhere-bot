from bot.config import SYSTEM_PROMPT
from bot.history import get_history, save_history
from bot.providers import generate


def ask_ai(user_id: int, user_message: str, context: str = "") -> str:
    """Answer `user_message` as the history teacher, using the user's saved
    conversation history.

    `context` (optional) is extra grounding — e.g. a Wikipedia article extract
    from /lookup — injected as a system message for THIS call only. It is sent
    to the model but deliberately NOT saved to history: the source text can be
    long and re-fetched, so persisting it would bloat the rolling history
    window. The user turn and the assistant reply are still saved as usual, so
    follow-up questions keep working.
    """
    history = get_history(user_id)
    history.append({"role": "user", "content": user_message})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "system", "content": context})
    messages += history

    reply = generate(user_id, messages)

    history.append({"role": "assistant", "content": reply})
    save_history(user_id, history)
    return reply
