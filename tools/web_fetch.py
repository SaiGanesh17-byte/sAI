"""
WebFetch: read a web page (docs, an issue, an API reference) as plain text.

The URL comes from the agent, so a prompt-injected agent could aim it at
localhost services, the LAN (router admin pages) or cloud metadata
(169.254.169.254). Hosts resolving to loopback/private/link-local/reserved
addresses are refused, and redirects are followed hop by hop so a public
page can't bounce the request somewhere internal. Opt in with the
web_fetch_allow_private setting to read e.g. docs on a local dev server.

Residual risk: DNS can change between the check and the connection (DNS
rebinding); closing that fully needs pinning the resolved IP per request.
"""
import ipaddress
import socket
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse

from tools.base import BaseTool

MAX_CHARS = 20000
TIMEOUT_SECONDS = 20
MAX_REDIRECTS = 5


def blocked_reason(url: str) -> Optional[str]:
    """Why fetching `url` is refused, or None if it's a public http(s) address."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "'url' must be a full http:// or https:// URL."
    from core.settings import load_settings
    if load_settings().get("web_fetch_allow_private", False):
        return None
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        return f"could not resolve {parsed.hostname}: {e}"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            return (f"{parsed.hostname} resolves to a private/internal address ({ip}). "
                    f"Set web_fetch_allow_private to true in settings to allow this.")
    return None


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
        import httpx

        current = url
        for _ in range(MAX_REDIRECTS + 1):
            reason = blocked_reason(current)
            if reason:
                return f"Error: refusing to fetch {current}: {reason}"
            try:
                # Redirects are followed manually so every hop is re-checked.
                resp = httpx.get(current, follow_redirects=False, timeout=TIMEOUT_SECONDS,
                                 headers={"User-Agent": "sAI/1.0 (+web_fetch)"})
            except httpx.HTTPError as e:
                return f"Error fetching {current}: {e}"
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                current = urljoin(current, resp.headers["location"])
                continue
            break
        else:
            return f"Error fetching {url}: more than {MAX_REDIRECTS} redirects."
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
        final = current
        header = f"URL: {final}" + (f" (redirected from {url})" if final != url else "")
        return f"{header}\n\n{text}"
