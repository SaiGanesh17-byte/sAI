"""
The context graph: what the agent loop knows about the code and the work, as a
graph, so each agent gets the *relevant* neighborhood instead of only a flat
history and a repo map.

    code layer       file --defines--> symbol, file --imports--> file,
                     test file --covers--> file          (from the repository index)
    work layer       file <- read / edited / created / error  (recorded by the
                     agent loop after every action; kept per project on disk)
    knowledge layer  entities from the Memory MCP server's per-project
                     knowledge_graph.jsonl (when that server is configured)

`neighborhood()` renders a compact prompt section for the current request:
the files it mentions (or whose symbols it mentions), files touched this
request, files named in errors -- each with what it defines, what it imports,
what imports it, its tests and its recent activity -- plus matching knowledge.
The `context_graph` tool answers the same questions on demand.
"""
import json
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

ACTIVITY_LIMIT = 300
REFRESH_SECONDS = 5.0
MAX_FOCUS_FILES = 6
SYMBOL_STOPWORDS = {"main", "test", "init", "run", "get", "set", "add", "update", "data", "app", "config",
                    "index", "utils", "helper", "handler", "setup", "the", "this", "file", "code", "error"}
CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt"}
TRACE_FILE = re.compile(r'File "([^"]+)", line (\d+)|([\w./-]+\.(?:py|js|jsx|ts|tsx|java|kt|go|rb)):(\d+)')


@dataclass
class FileNode:
    path: str
    symbols: List[tuple] = field(default_factory=list)  # (kind, name, line)
    imports: Set[str] = field(default_factory=set)      # resolved workspace files
    imported_by: Set[str] = field(default_factory=set)
    tests: Set[str] = field(default_factory=set)        # test files covering this file


class ContextGraph:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self.files: Dict[str, FileNode] = {}
        self.symbol_index: Dict[str, List[tuple]] = {}  # name -> [(file, line, kind)]
        self.activity: deque = deque(maxlen=ACTIVITY_LIMIT)
        self.turn = 0
        self._last_request = ""
        self._built_at = 0.0
        self._lock = threading.Lock()
        self._load_activity()

    # ------------------------------------------------------------------ persistence
    @property
    def _activity_file(self) -> Path:
        from memory.graphiti import workspace_memory_file
        return workspace_memory_file(self.workspace).parent / "context_activity.json"

    def _load_activity(self):
        try:
            data = json.loads(self._activity_file.read_text())
            self.activity.extend(data.get("activity", []))
            self.turn = int(data.get("turn", 0))
        except (OSError, ValueError):
            pass

    def _save_activity(self):
        try:
            self._activity_file.parent.mkdir(parents=True, exist_ok=True)
            self._activity_file.write_text(json.dumps({"turn": self.turn, "activity": list(self.activity)}))
        except OSError:
            pass

    # ------------------------------------------------------------------ code layer
    def refresh(self, force: bool = False) -> None:
        if not force and time.time() - self._built_at < REFRESH_SECONDS:
            return
        from repository.context import RepositoryContext
        try:
            repo = RepositoryContext.get_cached_context(self.workspace)
        except Exception:
            return
        files: Dict[str, FileNode] = {}
        symbol_index: Dict[str, List[tuple]] = {}
        raw_imports: Dict[str, List[str]] = {}
        for rel, entry in (repo.cache.data or {}).items():
            if not (self.workspace / rel).is_file():
                continue
            syms = (entry or {}).get("symbols") or {}
            node = FileNode(rel)
            for cls in syms.get("classes", []):
                node.symbols.append(("class", cls["name"], cls.get("line", 0)))
            for fn in syms.get("functions", []):
                node.symbols.append(("function", fn["name"], fn.get("line", 0)))
            for kind, name, line in node.symbols:
                symbol_index.setdefault(name, []).append((rel, line, kind))
            files[rel] = node
            raw_imports[rel] = syms.get("imports", [])
        for rel, imports in raw_imports.items():
            for imp in imports:
                target = self._resolve_import(rel, imp, files)
                if target and target != rel:
                    files[rel].imports.add(target)
                    files[target].imported_by.add(rel)
        for rel, node in files.items():
            subject = _tested_subject(rel, files)
            if subject:
                files[subject].tests.add(rel)
            # a test file also covers whatever it imports
            if _is_test_file(rel):
                for target in node.imports:
                    files[target].tests.add(rel)
        with self._lock:
            self.files, self.symbol_index, self._built_at = files, symbol_index, time.time()

    def _resolve_import(self, importer: str, imp: str, files: Dict[str, FileNode]) -> Optional[str]:
        base = Path(importer).parent
        candidates = []
        if imp.startswith("."):
            if "/" in imp:  # JS relative path
                stem = (base / imp).as_posix()
                candidates += [stem] + [stem + ext for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs")] + \
                              [f"{stem}/index{ext}" for ext in (".ts", ".tsx", ".js", ".jsx")]
            else:  # Python relative module ".store" / "..pkg.mod"
                level = len(imp) - len(imp.lstrip("."))
                pkg = base
                for _ in range(level - 1):
                    pkg = pkg.parent
                mod = imp.lstrip(".").replace(".", "/")
                candidates += [f"{(pkg / mod).as_posix()}.py", f"{(pkg / mod).as_posix()}/__init__.py"]
        else:
            mod = imp.replace(".", "/")
            candidates += [f"{mod}.py", f"{mod}/__init__.py", f"{mod}.java", f"{mod}.kt",
                           (base / f"{mod}.py").as_posix()]
            # Java/Kotlin: com.acme.orders.Customer -> .../com/acme/orders/Customer.java
            tail = mod + ".java"
            candidates += [f for f in files if f.endswith("/" + tail) or f == tail]
        for c in candidates:
            c = os.path.normpath(c)
            if c in files:
                return c
        return None

    # ------------------------------------------------------------------ work layer
    def note_request(self, text: str) -> None:
        text = (text or "").strip()
        if text and text != self._last_request:
            self._last_request = text
            self.turn += 1
            self._add({"kind": "request", "detail": text[:120]})

    def record_action(self, action: dict, outcome, agent_name: str = "") -> None:
        tool = action.get("tool", "")
        args = action.get("args") or {}
        status = getattr(outcome, "status", "ok")
        content = str(getattr(outcome, "content", "") or "")
        path = args.get("path") or args.get("script_path")
        rel = self._rel(path) if path else None
        if tool == "read_file" and rel and status == "ok":
            self._add({"kind": "read", "path": rel, "agent": agent_name})
        elif tool in ("write_file", "edit_file", "patch_file") and rel:
            kind = "edit" if status == "ok" else "edit-failed"
            if status == "ok" and tool == "write_file" and "Wrote" in content and not self._seen(rel):
                kind = "create"
            self._add({"kind": kind, "path": rel, "agent": agent_name,
                       "detail": "" if status == "ok" else content.splitlines()[0][:120] if content else ""})
        elif tool in ("execute_command", "run_python_script"):
            command = args.get("command") or f"python {path}"
            self._add({"kind": "command", "detail": f"{command[:80]} -> {status}", "agent": agent_name})
        # Errors anywhere in tool output that point at workspace files (tracebacks, compiler output).
        if status != "ok" or re.search(r"Traceback|Error|Exception|FAILED|failed", content):
            lines = content.splitlines()
            for i, line in enumerate(lines):
                for m in TRACE_FILE.finditer(line):
                    f, ln = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
                    frel = self._rel(f)
                    if frel and (self.workspace / frel).is_file():
                        after = lines[i + 1:]
                        summary = next((l.strip() for l in after if re.match(r"\s*[\w.]*(Error|Exception|Failure)\b", l)),
                                       next((l.strip() for l in after if re.search(r"Error|Exception|assert", l)), line.strip()))
                        self._add({"kind": "error", "path": frel, "line": int(ln), "detail": summary[:140]})
        self._save_activity()

    def _add(self, event: dict):
        event = {"turn": self.turn, "t": round(time.time()), **event}
        with self._lock:
            self.activity.append(event)

    def _seen(self, rel: str) -> bool:
        return any(e.get("path") == rel for e in self.activity)

    def _rel(self, path) -> Optional[str]:
        try:
            p = Path(str(path)).expanduser()
            p = (p if p.is_absolute() else self.workspace / p).resolve()
            return str(p.relative_to(self.workspace))
        except (ValueError, OSError):
            return None

    # ------------------------------------------------------------------ focus + rendering
    def focus(self, text: str) -> List[str]:
        """Files relevant to `text` (the request): mentioned by name/path/stem, defining a
        mentioned symbol, or touched / erroring in the current request."""
        self.refresh()
        found: List[str] = []
        lowered = text or ""
        for token in set(re.findall(r"[\w./-]+\.[A-Za-z]{1,5}\b", lowered)):
            rel = self._rel(token)
            if rel in self.files:
                found.append(rel)
        words = set(re.findall(r"[A-Za-z_][\w]{2,}", lowered))
        stems = {}
        for rel in self.files:
            if Path(rel).suffix in CODE_SUFFIXES:  # "tools" in a sentence shouldn't pull in docs/TOOLS.md
                stems.setdefault(Path(rel).stem.lower(), []).append(rel)
        for w in words:
            if w.lower() in stems and w.lower() not in SYMBOL_STOPWORDS:
                found.extend(stems[w.lower()][:2])
            if w in self.symbol_index and w.lower() not in SYMBOL_STOPWORDS:
                found.extend(f for f, _, _ in self.symbol_index[w][:2])
        for e in reversed(self.activity):
            if e.get("turn") != self.turn:
                break
            if e.get("path") in self.files:
                found.append(e["path"])
        if not found:
            # Nothing named explicitly ("how does the free-model fallback work?"): use the
            # repo's full-text index on the request's keywords to find where to start.
            found = self._keyword_files(text)
        return list(dict.fromkeys(found))[:MAX_FOCUS_FILES]

    _KEYWORD_STOP = {"what", "where", "when", "which", "how", "does", "happens", "step", "the", "and", "for", "with",
                     "this", "that", "from", "into", "edit", "anything", "dont", "don't", "name", "functions",
                     "involved", "implemented", "call", "calls", "called", "work", "works", "file", "files", "code",
                     "agent", "agents", "please", "explain", "tell", "show", "change", "make", "would", "should"}

    def _keyword_files(self, text: str, limit: int = 3) -> List[str]:
        import sqlite3
        words = [w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z_]{3,}", text or "")]
        terms = [w for w in dict.fromkeys(words) if w not in self._KEYWORD_STOP][:6]
        db = self.workspace / ".sai" / "codebase_fts.db"
        if len(terms) < 1 or not db.exists():
            return []
        query = " AND ".join(f'"{t}"' for t in terms)
        try:
            with sqlite3.connect(str(db)) as conn:
                rows = conn.execute("SELECT path FROM files_fts WHERE files_fts MATCH ? ORDER BY rank LIMIT ?",
                                    (query, limit * 3)).fetchall()
                if not rows and len(terms) > 1:  # too strict: any of the terms, best-ranked first
                    rows = conn.execute("SELECT path FROM files_fts WHERE files_fts MATCH ? ORDER BY rank LIMIT ?",
                                        (" OR ".join(f'"{t}"' for t in terms), limit * 3)).fetchall()
        except sqlite3.Error:
            return []
        code = [r[0] for r in rows if r[0] in self.files and Path(r[0]).suffix in CODE_SUFFIXES and not _is_test_file(r[0])]
        return code[:limit]

    def neighborhood(self, text: str, budget_chars: int = 2800) -> str:
        focus = self.focus(text)
        lines: List[str] = []
        mentioned = [w for w in dict.fromkeys(re.findall(r"[A-Za-z_]\w{2,}", text or ""))
                     if w in self.symbol_index and w.lower() not in SYMBOL_STOPWORDS]
        for w in mentioned[:4]:
            defs = ", ".join(f"{f}:{line}" for f, line, _ in self.symbol_index[w][:3])
            refs = self.references(w)
            lines.append(f"{w}: defined at {defs}; referenced at "
                         + (", ".join(refs[:10]) + (f" (+{len(refs) - 10} more)" if len(refs) > 10 else "") if refs else "no other place"))
        shown: Set[str] = set()
        for rel in focus:
            lines.append(self._describe(rel, detailed=True))
            shown.add(rel)
        related = []
        for rel in focus:
            node = self.files.get(rel)
            if node:
                related += sorted(node.imported_by | node.tests | node.imports)
        for rel in dict.fromkeys(related):
            if rel not in shown and len(shown) < MAX_FOCUS_FILES * 2:
                lines.append("  related: " + self._describe(rel, detailed=False))
                shown.add(rel)
        errors = [e for e in self.activity if e.get("kind") == "error" and self.turn - e.get("turn", 0) <= 2]
        if errors:
            lines.append("recent errors: " + "; ".join(
                f"{e['path']}:{e.get('line', '?')} {e.get('detail', '')} (turn {e['turn']})" for e in errors[-4:]))
        edits = [e for e in self.activity if e.get("kind") in ("edit", "create")]
        if edits:
            recent = list(dict.fromkeys(f"{e['path']} ({e['kind']}, turn {e['turn']})" for e in reversed(edits)))[:5]
            lines.append("changed recently: " + ", ".join(recent))
        knowledge = self._knowledge(text, focus)
        if knowledge:
            lines.append("project knowledge: " + knowledge)
        out = "\n".join(lines)
        return out[:budget_chars] + ("\n...[context graph truncated]" if len(out) > budget_chars else "")

    def _describe(self, rel: str, detailed: bool) -> str:
        node = self.files.get(rel)
        if node is None:
            return rel
        parts = []
        if node.symbols:
            syms = [f"{name}{'()' if kind == 'function' else ''} L{line}" for kind, name, line in node.symbols]
            parts.append("defines " + ", ".join(syms[:12] if detailed else syms[:5]) + (" …" if len(syms) > (12 if detailed else 5) else ""))
        if detailed:
            if node.imports:
                parts.append("imports " + ", ".join(sorted(node.imports)[:6]))
            if node.imported_by:
                parts.append("imported by " + ", ".join(sorted(node.imported_by)[:6]))
            if node.tests:
                parts.append("tests: " + ", ".join(sorted(node.tests)[:4]))
            acts = [e for e in self.activity if e.get("path") == rel and e.get("kind") != "request"]
            if acts:
                last = acts[-1]
                parts.append(f"last {last['kind']} turn {last['turn']}" + (f" ({last.get('detail')})" if last.get("kind") in ("error", "edit-failed") else ""))
        return f"{rel}: " + " | ".join(parts) if parts else rel

    def _knowledge(self, text: str, focus: List[str]) -> str:
        """Matching entities from the Memory MCP server's per-project knowledge graph file."""
        from memory.graphiti import workspace_memory_file
        kg = workspace_memory_file(self.workspace).parent / "knowledge_graph.jsonl"
        if not kg.exists():
            return ""
        terms = {w.lower() for w in re.findall(r"[A-Za-z_]\w{3,}", text or "")} | {Path(f).stem.lower() for f in focus}
        hits = []
        try:
            for line in kg.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                if item.get("type") != "entity":
                    continue
                blob = (item.get("name", "") + " " + " ".join(item.get("observations", []))).lower()
                if any(t in blob for t in terms):
                    obs = "; ".join(item.get("observations", [])[:2])
                    hits.append(f"{item.get('name')} [{item.get('entityType', '')}]: {obs}"[:160])
        except (OSError, ValueError):
            return ""
        return " | ".join(hits[:4])

    def references(self, name: str, with_text: bool = False) -> List[str]:
        """Every 'file:line' using `name` as a whole word, excluding its definitions."""
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        defs = {(f, line) for f, line, _ in self.symbol_index.get(name, [])}
        refs = []
        for f in sorted(self.files):
            if Path(f).suffix not in CODE_SUFFIXES:
                continue
            try:
                for i, line in enumerate((self.workspace / f).read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if pattern.search(line) and (f, i) not in defs:
                        refs.append(f"{f}:{i}: {line.strip()[:100]}" if with_text else f"{f}:{i}")
            except OSError:
                continue
        return refs

    # ------------------------------------------------------------------ tool query
    def query(self, name: str) -> str:
        self.refresh()
        name = (name or "").strip()
        rel = self._rel(name) if "." in name or "/" in name else None
        if rel in self.files:
            return self._describe(rel, detailed=True)
        if name in self.symbol_index:
            out = [f"{name} is defined in: " + ", ".join(f"{f}:{line} ({kind})" for f, line, kind in self.symbol_index[name][:5])]
            refs = self.references(name, with_text=True)
            out.append(f"referenced in {len(refs)} place(s):" + ("\n" + "\n".join(refs[:15]) if refs else " none"))
            defining = self.symbol_index[name][0][0]
            out.append("defining file: " + self._describe(defining, detailed=True))
            return "\n".join(out)
        stems = [f for f in self.files if Path(f).stem.lower() == name.lower()]
        if stems:
            return "\n".join(self._describe(f, detailed=True) for f in stems[:3])
        return f"Nothing named '{name}' in the context graph (indexed: {len(self.files)} files)."


def _is_test_file(rel: str) -> bool:
    name = Path(rel).name
    return bool(re.match(r"test_.*\.py$|.*_test\.(py|go)$|.*\.(test|spec)\.[jt]sx?$|.*Tests?\.(java|kt)$", name)) \
        or "/tests/" in f"/{rel}" or rel.startswith("tests/")


def _tested_subject(rel: str, files: Dict[str, FileNode]) -> Optional[str]:
    name = Path(rel).name
    m = re.match(r"test_(.+)\.py$|(.+)_test\.py$|(.+)\.(?:test|spec)\.([jt]sx?)$|(.+?)Tests?\.(java|kt)$", name)
    if not m:
        return None
    stem = next(g for g in (m.group(1), m.group(2), m.group(3), m.group(5)) if g)
    for f in files:
        if f != rel and Path(f).stem == stem and not _is_test_file(f):
            return f
    return None


_graphs: Dict[str, ContextGraph] = {}
_graphs_lock = threading.Lock()


def graph_for(workspace: Optional[Path] = None) -> ContextGraph:
    if workspace is None:
        from core.security import get_current_workspace
        workspace = get_current_workspace()
    key = str(Path(workspace).resolve())
    with _graphs_lock:
        if key not in _graphs:
            _graphs[key] = ContextGraph(Path(key))
        return _graphs[key]
