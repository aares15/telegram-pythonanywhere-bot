"""Source lookups for the /lookup command.

The teacher's default answers come from the AI model's own knowledge. This
module adds *grounded* lookups so a student can pull a topic from a citable
source:

  - WORLD history  → fetched live from the Wikipedia API and cited. Wikipedia
    is on PythonAnywhere's outbound whitelist, so this works on PA out of the
    box (unlike bot/news.py), and there is no whitelist on Vercel.
  - ARMENIAN history → deliberately NOT sourced from Wikipedia. The teacher
    answers from its own expertise and the bot points the student to dedicated
    Armenian sources (Armeniapedia, 100years100facts.com, Armenian-History.com).
    Those sites aren't whitelisted on PA and some block bots, so we LINK to them
    rather than fetch them.

Graceful degradation, same style as bot/news.py: any network / API error
returns None with a logged line rather than raising into the handler.
"""

from typing import Optional

import requests

from bot.config import (
    ARMENIAN_SOURCES,
    ARMENIAN_TOPIC_KEYWORDS,
    WIKI_API_URL,
    WIKI_MAX_EXTRACT,
    WIKI_REQUEST_TIMEOUT,
    WIKI_USER_AGENT,
)


def is_armenian_topic(text: str) -> bool:
    """True when `text` looks like an Armenian-history/culture topic.

    A deliberately simple, transparent, case-insensitive substring match
    against ARMENIAN_TOPIC_KEYWORDS (bot/config.py — extend it to widen
    coverage). When True, /lookup skips Wikipedia entirely.
    """
    low = (text or "").lower()
    return any(kw in low for kw in ARMENIAN_TOPIC_KEYWORDS)


def further_reading() -> str:
    """A short 'read more' block linking the trusted Armenian sources."""
    lines = ["📚 To dig deeper, these trusted Armenian sources are great:"]
    for name, url in ARMENIAN_SOURCES:
        lines.append(f"• {name} — {url}")
    return "\n".join(lines)


def wiki_lookup(topic: str) -> Optional[dict]:
    """Return {title, extract, url} for the best-matching Wikipedia article,
    or None on any failure. Never raises.

    Uses a single request with generator=search so the top hit's plain-text
    intro and canonical URL come back together. The extract is capped at
    WIKI_MAX_EXTRACT chars to bound how much is fed to the model.
    """
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": topic,
        "gsrlimit": 1,
        "prop": "extracts|info",
        "exintro": 1,
        "explaintext": 1,
        "inprop": "url",
        "redirects": 1,
    }
    try:
        resp = requests.get(
            WIKI_API_URL,
            params=params,
            timeout=WIKI_REQUEST_TIMEOUT,
            headers={"User-Agent": WIKI_USER_AGENT},
        )
        resp.raise_for_status()
        pages = ((resp.json().get("query") or {}).get("pages") or {})
    except Exception as e:
        print(f"Wikipedia lookup error: {e}")
        return None

    if not pages:
        return None
    # generator=search returns a dict keyed by page id; take the single hit.
    page = next(iter(pages.values()))
    title = (page.get("title") or "").strip()
    extract = (page.get("extract") or "").strip()
    url = (page.get("fullurl") or "").strip()
    if not title or not extract:
        return None
    if len(extract) > WIKI_MAX_EXTRACT:
        extract = extract[:WIKI_MAX_EXTRACT].rstrip() + "…"
    return {"title": title, "extract": extract, "url": url}
