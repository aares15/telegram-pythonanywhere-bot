from unittest.mock import MagicMock, patch


def _wiki_response(pages):
    """Fake a Wikipedia generator=search API response."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"query": {"pages": pages}}
    return resp


# ── is_armenian_topic ─────────────────────────────────────────────────────────


def test_is_armenian_topic_true_for_armenian_terms():
    from bot.lookup import is_armenian_topic

    assert is_armenian_topic("The Armenian Genocide")
    assert is_armenian_topic("tigranes the great")
    assert is_armenian_topic("kingdom of Urartu")
    assert is_armenian_topic("Artsakh")
    assert is_armenian_topic("Nikol Pashinyan")


def test_is_armenian_topic_false_for_world_terms():
    from bot.lookup import is_armenian_topic

    assert not is_armenian_topic("The Roman Empire")
    assert not is_armenian_topic("French Revolution")
    assert not is_armenian_topic("Genghis Khan")
    # A world genocide must NOT be misrouted to the Armenian path
    assert not is_armenian_topic("Rwandan genocide")


# ── further_reading ────────────────────────────────────────────────────────────


def test_further_reading_lists_sources():
    from bot.lookup import further_reading

    text = further_reading()
    assert "armeniapedia.org" in text
    assert "100years100facts.com" in text
    assert "armenian-history.com" in text


# ── wiki_lookup ────────────────────────────────────────────────────────────────


def test_wiki_lookup_parses_top_hit():
    pages = {
        "123": {
            "title": "Roman Empire",
            "extract": "The Roman Empire was the post-Republican state...",
            "fullurl": "https://en.wikipedia.org/wiki/Roman_Empire",
        }
    }
    with patch("bot.lookup.requests.get", return_value=_wiki_response(pages)) as mock_get:
        from bot.lookup import wiki_lookup

        article = wiki_lookup("Roman Empire")
        assert article["title"] == "Roman Empire"
        assert article["url"] == "https://en.wikipedia.org/wiki/Roman_Empire"
        assert "post-Republican" in article["extract"]
        # Plain-text intro of the top search hit is requested
        params = mock_get.call_args.kwargs["params"]
        assert params["generator"] == "search"
        assert params["gsrsearch"] == "Roman Empire"


def test_wiki_lookup_caps_extract():
    long_extract = "x" * 5000
    pages = {"1": {"title": "T", "extract": long_extract, "fullurl": "http://u"}}
    with (
        patch("bot.lookup.requests.get", return_value=_wiki_response(pages)),
        patch("bot.lookup.WIKI_MAX_EXTRACT", 100),
    ):
        from bot.lookup import wiki_lookup

        article = wiki_lookup("anything")
        # capped to WIKI_MAX_EXTRACT (+ the ellipsis marker)
        assert len(article["extract"]) <= 101
        assert article["extract"].endswith("…")


def test_wiki_lookup_none_when_no_pages():
    with patch("bot.lookup.requests.get", return_value=_wiki_response({})):
        from bot.lookup import wiki_lookup

        assert wiki_lookup("nonexistent gibberish") is None


def test_wiki_lookup_none_when_no_extract():
    pages = {"1": {"title": "T", "extract": "", "fullurl": "http://u"}}
    with patch("bot.lookup.requests.get", return_value=_wiki_response(pages)):
        from bot.lookup import wiki_lookup

        assert wiki_lookup("x") is None


def test_wiki_lookup_none_on_error():
    with patch("bot.lookup.requests.get", side_effect=Exception("network down")):
        from bot.lookup import wiki_lookup

        assert wiki_lookup("x") is None


# ── /lookup handler ─────────────────────────────────────────────────────────────


def test_cmd_lookup_empty_topic_shows_usage():
    with (
        patch("bot.handlers.wiki_lookup") as mock_wiki,
        patch("bot.handlers.ask_ai") as mock_ask,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_lookup
        from tests.test_handlers import make_message

        cmd_lookup(make_message(text="/lookup"))
        mock_wiki.assert_not_called()
        mock_ask.assert_not_called()
        assert "look up" in mock_bot.send_message.call_args[0][1].lower()


def test_cmd_lookup_armenian_topic_skips_wikipedia():
    with (
        patch("bot.handlers.wiki_lookup") as mock_wiki,
        patch("bot.handlers.ask_ai", return_value="Tigranes ruled...") as mock_ask,
        patch("bot.handlers.keep_typing"),
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import cmd_lookup
        from tests.test_handlers import make_message

        cmd_lookup(make_message(text="/lookup Tigranes the Great"))
        # Wikipedia must never be consulted for an Armenian topic
        mock_wiki.assert_not_called()
        mock_ask.assert_called_once_with(123, "Tigranes the Great")
        reply = mock_send.call_args[0][1]
        assert "Tigranes ruled..." in reply
        assert "armeniapedia.org" in reply  # further-reading block appended


def test_cmd_lookup_world_topic_grounds_and_cites():
    article = {
        "title": "Roman Empire",
        "extract": "The Roman Empire was...",
        "url": "https://en.wikipedia.org/wiki/Roman_Empire",
    }
    with (
        patch("bot.handlers.wiki_lookup", return_value=article) as mock_wiki,
        patch("bot.handlers.ask_ai", return_value="It was a big empire.") as mock_ask,
        patch("bot.handlers.keep_typing"),
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import cmd_lookup
        from tests.test_handlers import make_message

        cmd_lookup(make_message(text="/lookup Roman Empire"))
        mock_wiki.assert_called_once()
        # Grounding context is passed through to ask_ai
        assert "context" in mock_ask.call_args.kwargs
        assert "Roman Empire" in mock_ask.call_args.kwargs["context"]
        reply = mock_send.call_args[0][1]
        assert "It was a big empire." in reply
        assert "en.wikipedia.org/wiki/Roman_Empire" in reply
        assert "Wikipedia" in reply


def test_cmd_lookup_world_topic_wiki_unreachable_falls_back():
    with (
        patch("bot.handlers.wiki_lookup", return_value=None),
        patch("bot.handlers.ask_ai", return_value="From my own knowledge.") as mock_ask,
        patch("bot.handlers.keep_typing"),
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import cmd_lookup
        from tests.test_handlers import make_message

        cmd_lookup(make_message(text="/lookup Roman Empire"))
        # No grounding context available; answers from knowledge, no citation
        assert "context" not in mock_ask.call_args.kwargs
        reply = mock_send.call_args[0][1]
        assert reply == "From my own knowledge."


def test_cmd_lookup_armenian_wiki_hit_is_not_sourced():
    # Topic slips past the keyword filter, but the top Wikipedia hit turns out
    # to be an Armenian-history article — the safety net must reject it.
    article = {
        "title": "History of Armenia",
        "extract": "...",
        "url": "https://en.wikipedia.org/wiki/History_of_Armenia",
    }
    with (
        patch("bot.handlers.wiki_lookup", return_value=article),
        patch("bot.handlers.ask_ai", return_value="Long story.") as mock_ask,
        patch("bot.handlers.keep_typing"),
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import cmd_lookup
        from tests.test_handlers import make_message

        cmd_lookup(make_message(text="/lookup ancient highland kingdom"))
        # Fell back to the Armenian path: no grounding, further-reading appended,
        # and the Wikipedia URL is NOT cited.
        assert "context" not in mock_ask.call_args.kwargs
        reply = mock_send.call_args[0][1]
        assert "armeniapedia.org" in reply
        assert "wikipedia.org" not in reply


# ── ask_ai context grounding ────────────────────────────────────────────────────


def test_ask_ai_context_not_saved_to_history():
    with (
        patch("bot.ai.generate", return_value="reply") as mock_gen,
        patch("bot.ai.get_history", return_value=[]),
        patch("bot.ai.save_history") as mock_save,
    ):
        from bot.ai import ask_ai

        ask_ai(123, "Rome", context="SECRET SOURCE TEXT")
        # Context reaches the model...
        sent = mock_gen.call_args[0][1]
        assert any("SECRET SOURCE TEXT" in m["content"] for m in sent)
        # ...but is never persisted to history
        saved = mock_save.call_args[0][1]
        assert all("SECRET SOURCE TEXT" not in m["content"] for m in saved)
        assert saved[0] == {"role": "user", "content": "Rome"}
