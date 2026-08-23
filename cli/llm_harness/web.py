"""Web search via Tavily or Firecrawl API."""
import re

import httpx

TAVILY_URL = "https://api.tavily.com/search"
FIRECRAWL_URL = "https://api.firecrawl.dev/v2/search"


def _truncate(text: str, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _tavily_search(query: str, max_results: int, api_key: str) -> list[dict]:
    resp = httpx.post(
        TAVILY_URL,
        json={"query": query, "max_results": max_results, "search_depth": "basic"},
        headers={"Authorization": "Bearer " + api_key},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return [{"title": r.get("title", ""), "body": r.get("content", ""), "href": r.get("url", "")}
            for r in data.get("results", [])]


def _firecrawl_search(query: str, max_results: int, api_key: str | None) -> list[dict]:
    """Search Firecrawl v2; without a key Firecrawl still serves limited usage."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    resp = httpx.post(
        FIRECRAWL_URL,
        json={"query": query, "limit": max_results, "scrapeOptions": {"formats": ["markdown"]}},
        headers=headers,
        timeout=60.0,
    )
    if resp.status_code in (401, 403) and not api_key:
        raise ValueError("\u7f3a\u5c11 Firecrawl API key\uff1aconfig.toml \u7684 defaults.firecrawl_api_key \u6216\u73af\u5883\u53d8\u91cf FIRECRAWL_API_KEY")
    resp.raise_for_status()
    data = resp.json().get("data", {}) or {}
    rows = data if isinstance(data, list) else data.get("web") or data.get("results") or []
    out = []
    for r in rows:
        href = r.get("url") or r.get("href") or ""
        if not href:
            continue
        body = r.get("markdown") or r.get("description") or r.get("content") or r.get("body") or ""
        out.append({"title": r.get("title", ""), "body": _truncate(body), "href": href})
        if len(out) >= max_results:
            break
    return out


def web_search(query: str, max_results: int = 5, api_key: str | None = None,
               provider: str = "tavily") -> list[dict]:
    """Search and return list of {title, body, href} dicts."""
    if provider == "firecrawl":
        return _firecrawl_search(query, max_results, api_key)
    if not api_key:
        raise ValueError("\u7f3a\u5c11 Tavily API key\uff1aconfig.toml \u7684 defaults.tavily_api_key \u6216\u73af\u5883\u53d8\u91cf TAVILY_API_KEY")
    return _tavily_search(query, max_results, api_key)
