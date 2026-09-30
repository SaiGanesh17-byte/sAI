"""WebFetch: read a web page (docs, an issue, an API reference) as plain text."""
from typing import Any, Dict
from urllib.parse import urlparse

from tools.base import BaseTool

MAX_CHARS = 20000
TIMEOUT_SECONDS = 20


def html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    body = soup.find("main") or soup.find("article") or soup.body or soup
    lines = [ln.strip() for ln in body.get_text("\n").splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return f"# {title}\n\n{text}" if title else text


class WebFetchTool(BaseTool):
    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return ("Fetches a URL (http/https) and returns its readable text -- use it to read documentation, "
                "an issue or a page found with web_search. Long pages are truncated.")

    @property
    def permissions(self) -> list:
        return ["network"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full http(s) URL."}},
            "required": ["url"],
        }

    def execute(self, args: Dict[str, Any]) -> str:
        url = (args.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return "Error: 'url' must be a full http:// or https:// URL."
        import httpx

        try:
            resp = httpx.get(url, follow_redirects=True, timeout=TIMEOUT_SECONDS,
                             headers={"User-Agent": "sAI/1.0 (+web_fetch)"})
        except httpx.HTTPError as e:
            return f"Error fetching {url}: {e}"
        if resp.status_code >= 400:
            return f"Error fetching {url}: HTTP {resp.status_code}"

        ctype = resp.headers.get("content-type", "")
        if "html" in ctype:
            text = html_to_text(resp.text)
        elif ctype.startswith("text/") or "json" in ctype or "xml" in ctype or not ctype:
            text = resp.text
        else:
            return f"Error: {url} returned non-text content ({ctype})."

        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + f"\n...[truncated, {len(text) - MAX_CHARS} more chars]"
        final = str(resp.url)
        header = f"URL: {final}" + (f" (redirected from {url})" if final != url else "")
        return f"{header}\n\n{text}"
