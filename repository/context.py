from pathlib import Path
from typing import Dict, Any, List, Optional
from repository.scanner import RepositoryScanner
from repository.indexer import RepositoryIndexer
from repository.symbols import SymbolGraph
from repository.cache import RepositoryCache

import time

class RepositoryContext:
    """
    Unified Repository service providing codebase scanning, symbol graph indexing, 
    caching, and context map building.
    """
    _instances_cache = {}

    @classmethod
    def get_cached_context(cls, workspace_path: str | Path):
        path = Path(workspace_path).resolve()
        now = time.time()
        if path in cls._instances_cache:
            inst, cached_time = cls._instances_cache[path]
            if now - cached_time < 5.0:
                return inst
        inst = cls(path)
        cls._instances_cache[path] = (inst, now)
        return inst

    @classmethod
    def invalidate_cache(cls, workspace_path: str | Path):
        path = Path(workspace_path).resolve()
        if path in cls._instances_cache:
            del cls._instances_cache[path]

    def __init__(self, workspace_path: str | Path):
        self.workspace_path = Path(workspace_path).resolve()
        self.scanner = RepositoryScanner(self.workspace_path)
        self.symbols_graph = SymbolGraph()
        
        cache_path = self.workspace_path / ".sai" / "repo_cache.json"
        self.cache = RepositoryCache(cache_path)
        self.index_all()

    def index_all(self) -> None:
        self.symbols_graph.clear()
        files = self.scanner.scan()
        
        import sqlite3
        db_dir = self.workspace_path / ".sai"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "codebase_fts.db"
        
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(path, content);")
        conn.commit()
        
        cache_changed = False
        for f in files:
            rel_path_str = str(f.relative_to(self.workspace_path))
            try:
                mtime = f.stat().st_mtime
            except Exception:
                mtime = 0.0

            cached_entry = self.cache.get_file_cache(rel_path_str)
            
            # Seed or update FTS5 text content on cache miss
            if not cached_entry or cached_entry.get("mtime") != mtime:
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore")
                    cursor.execute("DELETE FROM files_fts WHERE path = ?;", (rel_path_str,))
                    cursor.execute("INSERT INTO files_fts(path, content) VALUES(?, ?);", (rel_path_str, content))
                    conn.commit()
                except Exception:
                    pass

            if cached_entry and cached_entry.get("mtime") == mtime:
                symbols = cached_entry.get("symbols", {})
            else:
                symbols = RepositoryIndexer.index_file(f)
                self.cache.set_file_cache(rel_path_str, mtime, symbols)
                cache_changed = True

            for cls in symbols.get("classes", []):
                self.symbols_graph.add_definition(cls["name"], rel_path_str, "class", cls)
            for fn in symbols.get("functions", []):
                self.symbols_graph.add_definition(fn["name"], rel_path_str, "function", fn)
            self.symbols_graph.add_imports(rel_path_str, symbols.get("imports", []))

        conn.close()
        if cache_changed:
            self.cache.save()

    def search_fts(self, query: str) -> str:
        import sqlite3
        db_path = self.workspace_path / ".sai" / "codebase_fts.db"
        if not db_path.exists():
            return "Codebase has not been indexed yet."
            
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute(
                "SELECT path, highlight(files_fts, 1, '<b>', '</b>') FROM files_fts WHERE files_fts MATCH ? ORDER BY rank LIMIT 8;",
                (query,)
            )
            rows = cursor.fetchall()
            conn.close()
            
            if not rows:
                return f"No matches found for query: '{query}'"
                
            results = []
            for path, highlighted_snippet in rows:
                lines = highlighted_snippet.splitlines()
                matching_lines = [l.strip() for l in lines if "<b>" in l][:3]
                snippet = "\n    ".join(matching_lines)
                results.append(f"📄 File: {path}\n    {snippet}\n")
                
            return "\n".join(results)
        except Exception as e:
            return f"Error executing FTS5 codebase search: {e}"

    def get_repo_map(self, max_chars: Optional[int] = None) -> str:
        """
        Generates a text-based tree summary of the repository with class/function symbol tables.

        This goes into *every* agent prompt, so with `max_chars` set it degrades
        instead of growing without bound (it was ~5k tokens -- over 70% of a
        typical agent prompt -- for this repo alone): full map if it fits, else
        the file tree without symbols, else a truncated file tree. Agents can
        still pull details on demand via list_directory / grep_ast /
        codebase_search.
        """
        full = self._build_repo_map(with_symbols=True)
        if max_chars is None or len(full) <= max_chars:
            return full

        hint = "\n...[repo map truncated to fit the prompt budget -- use list_directory, grep_ast or codebase_search for more]"
        tree_only = self._build_repo_map(with_symbols=False)
        if len(tree_only) + len(hint) <= max_chars:
            return tree_only + "\n[symbols omitted to fit the prompt budget -- use grep_ast to look them up]"

        cut = tree_only[: max(0, max_chars - len(hint))]
        cut = cut[: cut.rfind("\n")] if "\n" in cut else cut
        return cut + hint

    def _build_repo_map(self, with_symbols: bool) -> str:
        files = self.scanner.scan()
        if not files:
            return "Workspace is empty."

        tree_lines = []
        sorted_files = sorted([f.relative_to(self.workspace_path) for f in files])
        
        for rel_f in sorted_files:
            rel_str = str(rel_f)
            parts = rel_f.parts
            indent = "  " * (len(parts) - 1)
            tree_lines.append(f"{indent}├── {parts[-1]}")
            
            cached_entry = self.cache.get_file_cache(rel_str) if with_symbols else None
            if cached_entry:
                symbols = cached_entry.get("symbols", {})
                for cls in symbols.get("classes", []):
                    tree_lines.append(f"{indent}  class {cls['name']}")
                    for method in cls.get("methods", []):
                        tree_lines.append(f"{indent}    def {method}()")
                for fn in symbols.get("functions", []):
                    tree_lines.append(f"{indent}  def {fn['name']}()")

        return "\n".join(tree_lines)
