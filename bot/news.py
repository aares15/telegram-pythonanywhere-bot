"""Fetch news for the /newsArmenia and /newsWorldwide commands.

Uses GNews (https://gnews.io) by default — a free news API. Two endpoints:
  - /search        → articles matching a query        (Armenia news)
  - /top-headlines → ranked current headlines by topic (world news)
Both are configurable via env so a student can swap in NewsAPI.org or any
other service with a compatible JSON shape (see bot/config.py).

Graceful degradation, same style as bot/notes.py: when NEWS_API_KEY is
unset the module reports "not configured", and any network / API error
returns None with a logged line rather than raising into the handler.

PA caveat: GNews's domain is NOT on PythonAnywhere's free-tier outbound
whitelist. This works locally out of the box; to run it on PA you must
request the news API's domain be whitelisted on the PA forums, otherwise
the request fails fast and the command reports it couldn't reach the source.
"""

from typing import Optional

import requests

from bot.config import (
    NEWS_API_KEY,
    NEWS_API_URL,
    NEWS_LANG,
    NEWS_QUERY,
    NEWS_REQUEST_TIMEOUT,
    NEWS_WORLD_API_URL,
    NEWS_WORLD_CATEGORY,
)


def news_configured() -> bool:
    """True when a news API key is set, so the handlers can tell the user
    whether the news commands are switched on for this deployment."""
    return bool(NEWS_API_KEY)


def _fetch(url: str, params: dict, count: int) -> Optional[list[dict]]:
    """Call a GNews-compatible endpoint and return up to `count` parsed
    items, top/newest first.

    Each item is a dict with `title`, `source`, and `url`. Returns None
    on any failure (not configured, network error, bad response) — the
    caller decides what to tell the user. Never raises.
    """
    if not NEWS_API_KEY:
        return None
    try:
        resp = requests.get(url, params=params, timeout=NEWS_REQUEST_TIMEOUT)
        resp.raise_for_status()
        articles = resp.json().get("articles", []) or []
    except Exception as e:
        print(f"News fetch error: {e}")
        return None

    items: list[dict] = []
    for article in articles[:count]:
        title = (article.get("title") or "").strip()
        if not title:
            continue
        source = ((article.get("source") or {}).get("name") or "").strip()
        link = (article.get("url") or "").strip()
        items.append({"title": title, "source": source, "url": link})
    return items


def get_top_news(count: int = 3) -> Optional[list[dict]]:
    """Return up to `count` latest Armenia news items, newest first.

    Uses the /search endpoint with NEWS_QUERY (Armenia-focused). Returns
    None on any failure. Never raises.
    """
    return _fetch(
        NEWS_API_URL,
        {
            "q": NEWS_QUERY,
            "lang": NEWS_LANG,
            "sortby": "publishedAt",
            "max": count,
            "apikey": NEWS_API_KEY,
        },
        count,
    )


def get_world_news(count: int = 3) -> Optional[list[dict]]:
    """Return up to `count` top current world headlines.

    Uses the /top-headlines endpoint (NEWS_WORLD_CATEGORY) — the interesting
    stories going on globally right now, ranked, with no search term. Returns
    None on any failure. Never raises.
    """
    return _fetch(
        NEWS_WORLD_API_URL,
        {
            "category": NEWS_WORLD_CATEGORY,
            "lang": NEWS_LANG,
            "max": count,
            "apikey": NEWS_API_KEY,
        },
        count,
    )
