from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SEARXNG_URL = os.getenv("SEARXNG_URL", "http://127.0.0.1:8888").rstrip("/")
WEB_SEARCH_MAX_RESULTS = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "8"))
WEB_SEARCH_TIMEOUT_SECONDS = int(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", "8"))
WEB_SEARCH_CACHE_TTL_SECONDS = int(os.getenv("WEB_SEARCH_CACHE_TTL_SECONDS", "900"))
WEB_SEARCH_CONTEXT_CHAR_LIMIT = int(os.getenv("WEB_SEARCH_CONTEXT_CHAR_LIMIT", "6000"))

_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}


class WebSearchError(RuntimeError):
    pass


def _cache_key(query: str) -> str:
    return " ".join(query.lower().split())


def _clean_text(value: Any, *, limit: int = 600) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())[:limit]


def searxng_search(query: str, *, max_results: int = WEB_SEARCH_MAX_RESULTS) -> list[dict[str, str]]:
    query = query.strip()
    if not query:
        return []

    key = _cache_key(query)
    cached = _CACHE.get(key)
    now = time.time()
    if cached and now - cached[0] < WEB_SEARCH_CACHE_TTL_SECONDS:
        return cached[1][:max_results]

    params = urlencode(
        {
            "q": query,
            "format": "json",
            "language": "ru-RU",
            "safesearch": "0",
        }
    )
    req = Request(
        f"{SEARXNG_URL}/search?{params}",
        headers={"Accept": "application/json", "User-Agent": "UDBBot/1.0"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=WEB_SEARCH_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise WebSearchError(f"SearXNG search failed: {exc}") from exc

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        raise WebSearchError("SearXNG returned response without results list")

    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = _clean_text(item.get("url"), limit=500)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        title = _clean_text(item.get("title"), limit=200)
        content = _clean_text(item.get("content") or item.get("snippet"), limit=700)
        engine = _clean_text(item.get("engine"), limit=80)
        published_date = _clean_text(item.get("publishedDate") or item.get("published_date"), limit=80)
        if not title and not content:
            continue
        results.append(
            {
                "title": title,
                "url": url,
                "content": content,
                "engine": engine,
                "published_date": published_date,
            }
        )
        if len(results) >= max_results:
            break

    _CACHE[key] = (now, results)
    return results


def build_web_context(*, question: str, search_plan: dict[str, Any]) -> str:
    queries = [str(q).strip() for q in search_plan.get("queries", []) if str(q).strip()]
    needed_facts = [str(f).strip() for f in search_plan.get("needed_facts", []) if str(f).strip()]
    answer_strategy = str(search_plan.get("answer_strategy") or "").strip()
    if not queries:
        raise WebSearchError("Search plan has no queries")

    all_results: list[tuple[str, dict[str, str]]] = []
    seen_urls: set[str] = set()
    per_query_limit = max(2, WEB_SEARCH_MAX_RESULTS)
    for query in queries:
        for result in searxng_search(query, max_results=per_query_limit):
            url = result.get("url") or ""
            if url in seen_urls:
                continue
            seen_urls.add(url)
            all_results.append((query, result))
            if len(all_results) >= WEB_SEARCH_MAX_RESULTS:
                break
        if len(all_results) >= WEB_SEARCH_MAX_RESULTS:
            break

    if not all_results:
        raise WebSearchError("SearXNG returned no usable results")

    lines = [
        f"Исходный вопрос: {question}",
        f"Дата поиска: {time.strftime('%Y-%m-%d')}",
        "Что нужно проверить:",
    ]
    lines.extend(f"- {fact}" for fact in needed_facts[:5])
    if answer_strategy:
        lines.append(f"Стратегия ответа: {answer_strategy}")
    lines.append("Поисковые запросы:")
    lines.extend(f"- {query}" for query in queries[:3])
    lines.append("Результаты поиска:")
    for index, (query, result) in enumerate(all_results, start=1):
        lines.append(f"{index}. query: {query}")
        lines.append(f"   title: {result.get('title') or '(без заголовка)'}")
        if result.get("published_date"):
            lines.append(f"   date: {result['published_date']}")
        if result.get("engine"):
            lines.append(f"   engine: {result['engine']}")
        lines.append(f"   snippet: {result.get('content') or '(нет сниппета)'}")
        lines.append(f"   url: {result.get('url')}")

    context = "\n".join(lines)
    return context[:WEB_SEARCH_CONTEXT_CHAR_LIMIT]
