"""Юнит-тесты опциональных способностей друна: веб-доступ (#11), картинки (#10).

Проверяем чистые куски без сети/БД: парсер результатов поиска и предохранители
(``web_usable`` / ``image_usable``) — что по умолчанию всё выключено.
"""

from __future__ import annotations

from app.features.drun import websearch
from app.features.drun.config import AiConfig


def _cfg(**over) -> AiConfig:
    base = dict(
        enabled=True, base_url="https://api.openai.com/v1", api_key="k",
        model="m", fast_model="", models_by_role={}, temperature=0.9,
        max_tokens=600, posts_per_day_max=6, min_severity=2,
        autonomous_enabled=False,
        reply_enabled=True, reply_cooldown_sec=20, name_triggers=["друн"],
        random_butt_in_chance=0.0, econ_enabled=False, econ_max_pct=0.05,
        econ_max_abs=1000, econ_cooldown_sec=7200, econ_daily_cap=20,
    )
    base.update(over)
    return AiConfig(**base)


def test_web_disabled_by_default():
    assert _cfg().web_usable is False


def test_web_usable_needs_url():
    assert _cfg(web_enabled=True).web_usable is False
    assert _cfg(web_enabled=True, web_search_url="http://s/").web_usable is True


def test_image_disabled_by_default():
    assert _cfg().image_usable is False


def test_image_usable_requires_endpoint_and_model():
    assert _cfg(image_enabled=True).image_usable is False
    ok = _cfg(
        image_enabled=True, image_base_url="http://i", image_model="gpt-image-1",
    )
    # ключ берётся из api_key как фолбэк
    assert ok.image_usable is True


def test_extract_searxng_results():
    data = {
        "results": [
            {"title": "Foo", "content": "bar baz", "url": "http://x"},
            {"title": "Q", "snippet": "alt field"},
            {"nope": 1},
        ]
    }
    items = websearch._extract(data)
    assert items[0]["title"] == "Foo"
    assert items[0]["snippet"] == "bar baz"
    assert items[1]["snippet"] == "alt field"
    assert len(items) == 2


def test_extract_bad_shape():
    assert websearch._extract({"results": "nope"}) == []
    assert websearch._extract([]) == []


def test_extract_weather_location():
    assert websearch._extract_weather_location("какая погода в Амстердаме?") == "Амстердаме"
    assert websearch._extract_weather_location("температура в Санкт-Петербурге сейчас") == "Санкт-Петербурге"
    assert websearch._extract_weather_location("здарова друн") == ""


def test_format_weather_summary():
    data = {
        "current_condition": [{
            "temp_C": "18",
            "FeelsLikeC": "17",
            "humidity": "70",
            "windspeedKmph": "12",
            "lang_ru": [{"value": "Переменная облачность"}],
        }],
        "weather": [{"hourly": [{"chanceofrain": "20"}]}],
    }
    out = websearch._format_weather(data, "Amsterdam")
    assert "Amsterdam: 18°C" in out
    assert "ощущается как 17°C" in out
    assert "шанс дождя 20%" in out


def test_rewrite_fresh_queries():
    assert websearch._rewrite_query("новости ИИ") == "новости ИИ сегодня"
    assert websearch._rewrite_query("курс евро") == "курс евро сейчас"
    assert websearch._rewrite_query("что такое pgvector") == "что такое pgvector"


def test_html_to_summary_extracts_page_content():
    html = """
    <html><head>
      <title>Example Title</title>
      <meta name="description" content="Short useful description.">
      <style>.x{}</style><script>bad()</script>
    </head><body>
      <nav>menu</nav>
      <p>This is a long enough paragraph with actual useful page content that
      should be visible to the retrieval layer and not hidden in tags.</p>
    </body></html>
    """
    title, snippet = websearch._html_to_summary(html)
    assert title == "Example Title"
    assert "Short useful description" in snippet
    assert "actual useful page content" in snippet
    assert "bad()" not in snippet


def test_ssrf_guard_rejects_non_https():
    from app.features.drun import provider

    for bad in ("http://example.com/x.png", "ftp://h/x", "file:///etc/passwd"):
        try:
            provider._assert_safe_public_url(bad)
            assert False, f"expected rejection for {bad}"
        except provider.LlmError:
            pass


def test_ssrf_guard_rejects_private_and_loopback():
    from app.features.drun import provider

    # Резолвится в loopback/приватные адреса — должно отклоняться.
    for bad in ("https://localhost/x.png", "https://127.0.0.1/x.png"):
        try:
            provider._assert_safe_public_url(bad)
            assert False, f"expected rejection for {bad}"
        except provider.LlmError:
            pass
