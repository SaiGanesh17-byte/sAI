from typing import Dict, Any
from tools.base import BaseTool
from duckduckgo_search import DDGS
import warnings

# Suppress the duckduckgo_search package rename runtime warning
warnings.filterwarnings("ignore", category=RuntimeWarning, module="duckduckgo_search")

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

    def execute(self, args: Dict[str, Any]) -> str:
        query = args.get("query")
        if not query:
            return "Error: 'query' argument is required."

        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))
            if not results:
                return f"No search results found for query: '{query}'"
            
            output = []
            for r in results:
                output.append(f"Title: {r.get('title')}\nURL: {r.get('href')}\nBody: {r.get('body')}\n---")
            return "\n".join(output)
        except Exception as e:
            return f"Error performing web search: {e}"

import ast
from pathlib import Path

WORKSPACE_ROOT = Path("/Users/saiganeshongolu/sAI").resolve()

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
        # Walk Python files
        for path in WORKSPACE_ROOT.rglob("*.py"):
            if "venv" in path.parts or ".git" in path.parts:
                continue
            try:
                node = ast.parse(path.read_text(encoding="utf-8"))
                for child in ast.walk(node):
                    if isinstance(child, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                        if pattern in child.name.lower():
                            matches.append(f"File: {path.relative_to(WORKSPACE_ROOT)} | Line {child.lineno} | {type(child).__name__}: {child.name}")
            except Exception:
                pass
                
        # Walk Java, JS, TS, HTML, CSS files
        for path in WORKSPACE_ROOT.rglob("*"):
            if path.suffix in [".java", ".js", ".html", ".css", ".ts", ".xml"]:
                if "venv" in path.parts or ".git" in path.parts or "node_modules" in path.parts:
                    continue
                try:
                    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                        if (("class " in line or "void " in line or "function " in line or "@" in line) and pattern in line.lower()):
                            matches.append(f"File: {path.relative_to(WORKSPACE_ROOT)} | Line {i} | LineContent: {line.strip()}")
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
                repo_ctx = RepositoryContext.get_cached_context(WORKSPACE_ROOT)
                
            return repo_ctx.search_fts(query)
        except Exception as e:
            return f"Error executing codebase full-text search: {e}"
