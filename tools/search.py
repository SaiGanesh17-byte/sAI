from typing import Dict, Any
from tools.base import BaseTool
from ddgs import DDGS
from ddgs.exceptions import RatelimitException, TimeoutException
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

class SearchTool(BaseTool):
    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Performs a DuckDuckGo web search to retrieve documentation or error help."

    @property
    def permissions(self) -> list:
        return ["network"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."}
            },
            "required": ["query"]
        }

    # DDG's free/unauthenticated search endpoint rate-limits aggressively under
    # back-to-back calls (a WebSearch agent turn commonly fires 2-3 in a row) --
    # retry only the transient failure classes, not a permanently bad query.
    @retry(
        retry=retry_if_exception_type((RatelimitException, TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        reraise=True,
    )
    def _search(self, query: str, max_results: int) -> list:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    def execute(self, args: Dict[str, Any]) -> str:
        query = args.get("query")
        if not query:
            return "Error: 'query' argument is required."

        try:
            results = self._search(query, max_results=6)
        except Exception as e:
            results, ddg_error = [], str(e)
        else:
            ddg_error = "" if results else "no results"

        if results:
            output = []
            for r in results:
                output.append(f"Title: {r.get('title')}\nURL: {r.get('href')}\nBody: {r.get('body')}\n---")
            return "\n".join(output)

        # DuckDuckGo scraping gets blocked or rate-limited (every backend returned
        # "No results found" after a busy day); fall back to OpenRouter's web search.
        fallback = openrouter_web_search(query)
        if fallback:
            return fallback
        return (f"Error performing web search: {ddg_error or 'no results'}. Try a differently-phrased query, or "
                f"web_fetch an official source directly (project website, docs, GitHub releases page).")

import ast
from pathlib import Path


def openrouter_web_search(query: str, max_results: int = 5) -> str:
    """
    Search via OpenRouter's web plugin (billed to the OpenRouter key, ~$0.007 per
    search). Used only when DuckDuckGo fails. Returns "" if unavailable.
    """
    import os
    from core.settings import load_settings
    settings = load_settings()
    if settings.get("provider") != "openrouter" or not settings.get("web_search_fallback", True):
        return ""
    if not os.getenv("OPENROUTER_API_KEY"):
        return ""
    try:
        from openai import OpenAI
        from llm.tracker import token_tracker
        client = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url="https://openrouter.ai/api/v1")
        resp = client.chat.completions.create(
            model=f"{settings.get('free_fallback_model') or 'openai/gpt-4o-mini'}:online",
            messages=[{"role": "user", "content":
                       f"Search the web for: {query}\nList each result as:\nTitle: ...\nURL: ...\nBody: what the "
                       "page says that is relevant (facts, versions, dates), quoted closely.\n---\n"
                       "Report only what the results say."}],
            temperature=0, timeout=45,
            extra_body={"plugins": [{"id": "web", "max_results": max_results}]},
        )
        if getattr(resp, "usage", None):
            token_tracker.add_usage(resp.usage)
        text = (resp.choices[0].message.content or "").strip()
        return f"[web search via OpenRouter -- DuckDuckGo was unavailable]\n{text}" if text else ""
    except Exception:
        return ""

from core.security import get_current_workspace

class GrepAstTool(BaseTool):
    @property
    def name(self) -> str:
        return "grep_ast"

    @property
    def description(self) -> str:
        return "Scans codebase ASTs for classes, functions, or variable definitions matching a pattern."

    @property
    def permissions(self) -> list:
        return ["read"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Class, function, or method name keyword to search for (case-insensitive)."}
            },
            "required": ["pattern"]
        }

    def execute(self, args: Dict[str, Any]) -> str:
        pattern = args.get("pattern", "").lower()
        if not pattern:
            return "Error: 'pattern' argument is required."

        matches = []
        root = get_current_workspace()
        # Walk Python files
        for path in root.rglob("*.py"):
            if "venv" in path.parts or ".git" in path.parts:
                continue
            try:
                node = ast.parse(path.read_text(encoding="utf-8"))
                for child in ast.walk(node):
                    if isinstance(child, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                        if pattern in child.name.lower():
                            matches.append(f"File: {path.relative_to(root)} | Line {child.lineno} | {type(child).__name__}: {child.name}")
            except Exception:
                pass
                
        # Walk Java, JS, TS, HTML, CSS files
        for path in root.rglob("*"):
            if path.suffix in [".java", ".js", ".html", ".css", ".ts", ".xml"]:
                if "venv" in path.parts or ".git" in path.parts or "node_modules" in path.parts:
                    continue
                try:
                    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                        if (("class " in line or "void " in line or "function " in line or "@" in line) and pattern in line.lower()):
                            matches.append(f"File: {path.relative_to(root)} | Line {i} | LineContent: {line.strip()}")
                except Exception:
                    pass

        if not matches:
            return f"No definitions found matching pattern '{pattern}'."
        return "\n".join(matches[:40])

class CodebaseSearchTool(BaseTool):
    @property
    def name(self) -> str:
        return "codebase_search"

    @property
    def description(self) -> str:
        return "Searches all project files for text content using a full-text search query (supports standard boolean search tags like AND, OR, NOT)."

    @property
    def permissions(self) -> list:
        return ["read"]

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "FTS search query pattern (e.g. 'Lock AND CURRENT_ACTIVITY')."}
            },
            "required": ["query"]
        }

    def execute(self, args: Dict[str, Any]) -> str:
        query = args.get("query", "").strip()
        if not query:
            return "Error: 'query' argument is required."
            
        try:
            from core.kernel import kernel
            repo_ctx = kernel.get_service("repository")
            if not repo_ctx:
                from repository.context import RepositoryContext
                repo_ctx = RepositoryContext.get_cached_context(get_current_workspace())
                
            return repo_ctx.search_fts(query)
        except Exception as e:
            return f"Error executing codebase full-text search: {e}"
