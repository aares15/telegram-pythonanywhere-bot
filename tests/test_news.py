from unittest.mock import MagicMock, patch


def _fake_response(articles):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"articles": articles}
    return resp


# ── get_top_news ────────────────────────────────────────────────────────────


def test_get_top_news_returns_none_without_key():
    with patch("bot.news.NEWS_API_KEY", ""):
        from bot.news import get_top_news

        assert get_top_news() is None


def test_get_top_news_parses_articles():
    articles = [
        {"title": "One", "source": {"name": "SrcA"}, "url": "http://a"},
        {"title": "Two", "source": {"name": "SrcB"}, "url": "http://b"},
        {"title": "Three", "source": {"name": "SrcC"}, "url": "http://c"},
    ]
    with (
        patch("bot.news.NEWS_API_KEY", "key"),
        patch("bot.news.requests.get", return_value=_fake_response(articles)) as mock_get,
    ):
        from bot.news import get_top_news

        items = get_top_news(3)
        assert items == [
            {"title": "One", "source": "SrcA", "url": "http://a"},
            {"title": "Two", "source": "SrcB", "url": "http://b"},
            {"title": "Three", "source": "SrcC", "url": "http://c"},
        ]
        # count is passed to the API as `max`
        assert mock_get.call_args.kwargs["params"]["max"] == 3


def test_get_top_news_respects_count():
    articles = [{"title": f"n{i}", "source": {"name": "s"}, "url": f"u{i}"} for i in range(10)]
    with (
        patch("bot.news.NEWS_API_KEY", "key"),
        patch("bot.news.requests.get", return_value=_fake_response(articles)),
    ):
        from bot.news import get_top_news

        assert len(get_top_news(3)) == 3


def test_get_top_news_skips_titleless_articles():
    articles = [
        {"title": "", "source": {"name": "s"}, "url": "u"},
        {"title": "Real", "source": {"name": "s"}, "url": "u"},
    ]
    with (
        patch("bot.news.NEWS_API_KEY", "key"),
        patch("bot.news.requests.get", return_value=_fake_response(articles)),
    ):
        from bot.news import get_top_news

        items = get_top_news(3)
        assert [i["title"] for i in items] == ["Real"]


def test_get_top_news_returns_none_on_error():
    with (
        patch("bot.news.NEWS_API_KEY", "key"),
        patch("bot.news.requests.get", side_effect=Exception("network down")),
    ):
        from bot.news import get_top_news

        assert get_top_news() is None


def test_news_configured_reflects_key():
    from bot import news

    with patch.object(news, "NEWS_API_KEY", "key"):
        assert news.news_configured() is True
    with patch.object(news, "NEWS_API_KEY", ""):
        assert news.news_configured() is False


# ── /news handler ─────────────────────────────────────────────────────────────


def test_cmd_news_not_configured():
    with (
        patch("bot.handlers.news_configured", return_value=False),
        patch("bot.handlers.get_top_news") as mock_get,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_news
        from tests.test_handlers import make_message

        cmd_news(make_message(text="/news"))
        mock_get.assert_not_called()
        assert "isn't set up" in mock_bot.send_message.call_args[0][1]


def test_cmd_news_sends_headlines():
    items = [
        {"title": "Headline one", "source": "SrcA", "url": "http://a"},
        {"title": "Headline two", "source": "SrcB", "url": "http://b"},
        {"title": "Headline three", "source": "SrcC", "url": "http://c"},
    ]
    with (
        patch("bot.handlers.news_configured", return_value=True),
        patch("bot.handlers.get_top_news", return_value=items),
        patch("bot.handlers.keep_typing"),
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import cmd_news
        from tests.test_handlers import make_message

        cmd_news(make_message(text="/news"))
        sent = mock_send.call_args[0][1]
        assert "Headline one" in sent
        assert "Headline three" in sent
        assert "SrcA" in sent


def test_cmd_news_fetch_failure():
    with (
        patch("bot.handlers.news_configured", return_value=True),
        patch("bot.handlers.get_top_news", return_value=None),
        patch("bot.handlers.keep_typing"),
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import cmd_news
        from tests.test_handlers import make_message

        cmd_news(make_message(text="/news"))
        mock_send.assert_not_called()
        assert "Couldn't fetch" in mock_bot.send_message.call_args[0][1]
