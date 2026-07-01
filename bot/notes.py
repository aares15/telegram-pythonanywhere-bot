from typing import Optional

from bot.clients import store


def save_note(user_id: int, note: str) -> bool:
    """Save a single note for the user. Returns True on success.

    Returns False if storage is not configured (stateless mode) or the
    store write fails, so the handler can tell the user memory is off.
    """
    if store is None:
        return False
    try:
        store.set(f"note:{user_id}", note)
        return True
    except Exception as e:
        print(f"Store write error (notes): {e}")
        return False


def get_note(user_id: int) -> Optional[str]:
    """Return the user's saved note, or None if there isn't one.

    Also returns None if storage is not configured or the read fails.
    """
    if store is None:
        return None
    try:
        return store.get(f"note:{user_id}")
    except Exception as e:
        print(f"Store read error (notes): {e}")
        return None


def delete_note(user_id: int) -> bool:
    """Delete the user's saved note. Returns True on success.

    Returns False if storage is not configured or the delete fails.
    Deleting a note that doesn't exist is a no-op and still succeeds.
    """
    if store is None:
        return False
    try:
        store.delete(f"note:{user_id}")
        return True
    except Exception as e:
        print(f"Store delete error (notes): {e}")
        return False
