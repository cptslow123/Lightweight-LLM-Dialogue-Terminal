"""Web search via Tavily API."""
import httpx

TAVILY_URL = "https://api.tavily.com/search"


def web_search(query: str, max_results: int = 5, api_key: str | None = None) -> list[dict]:
    """Search Tavily and return list of {title, body, href} dicts."""
    if not api_key:
        raise ValueError("\u7f3a\u5c11 Tavily API key\uff1aconfig.toml \u7684 defaults.tavily_api_key \u6216\u73af\u5883\u53d8\u91cf TAVILY_API_KEY")
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
