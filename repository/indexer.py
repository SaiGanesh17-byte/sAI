import ast
from pathlib import Path
from typing import Dict, List, Any

# Bump when indexing output changes so cached entries are rebuilt.
# 2: JS/TS + Java symbols, relative Python imports.
INDEX_VERSION = 2


class RepositoryIndexer:
    @staticmethod
    def index_file(file_path: Path) -> Dict[str, Any]:
        """
        Extract classes, functions, and symbols from a code file.
        Focuses on Python AST indexing with a simple fallback.
        """
        if file_path.suffix == '.py':
            return RepositoryIndexer._index_python(file_path)
        if file_path.suffix in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'):
            return RepositoryIndexer._index_js(file_path)
        if file_path.suffix in ('.java', '.kt'):
            return RepositoryIndexer._index_java(file_path)
        return RepositoryIndexer._index_generic(file_path)

    @staticmethod
    def _index_python(file_path: Path) -> Dict[str, Any]:
        symbols = {
            "classes": [],
            "functions": [],
            "imports": []
        }
        try:
            content = file_path.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(file_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    symbols["classes"].append({
                        "name": node.name,
                        "line": node.lineno,
                        "methods": [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
                    })
                elif isinstance(node, ast.FunctionDef):
                    symbols["functions"].append({
                        "name": node.name,
                        "line": node.lineno,
                        "args": [arg.arg for arg in node.args.args]
                    })
                elif isinstance(node, ast.Import):
                    for name in node.names:
                        symbols["imports"].append(name.name)
                elif isinstance(node, ast.ImportFrom):
                    # Keep relative imports ("from .store import x" -> ".store") so the
                    # context graph can resolve them against the importing file.
                    prefix = "." * (node.level or 0)
                    if node.module:
                        symbols["imports"].append(prefix + node.module)
                    elif prefix:
                        symbols["imports"].extend(prefix + a.name for a in node.names)
        except Exception:
            pass
        return symbols

    @staticmethod
    def _index_js(file_path: Path) -> Dict[str, Any]:
        """Regex-level JS/TS indexing: enough for the context graph (definitions, imports)."""
        import re
        symbols = {"classes": [], "functions": [], "imports": []}
        try:
            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return symbols
        fn = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"
                        r"|^\s*(?:export\s+)?(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")
        cls = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)")
        imp = re.compile(r"""(?:import\s[^'"]*?from\s*|import\s*|require\(\s*)['"]([^'"]+)['"]""")
        for i, line in enumerate(lines, 1):
            m = cls.match(line)
            if m:
                symbols["classes"].append({"name": m.group(1), "line": i, "methods": []})
                continue
            m = fn.match(line)
            if m:
                symbols["functions"].append({"name": m.group(1) or m.group(2), "line": i, "args": []})
            for target in imp.findall(line):
                symbols["imports"].append(target)
        return symbols

    @staticmethod
    def _index_java(file_path: Path) -> Dict[str, Any]:
        import re
        symbols = {"classes": [], "functions": [], "imports": []}
        try:
            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return symbols
        cls = re.compile(r"^\s*(?:(?:public|private|protected|abstract|final|static|sealed|open|data)\s+)*(?:class|interface|enum|record|object)\s+([A-Za-z_]\w*)")
        method = re.compile(r"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized|override|suspend)\s+)+(?:<[^>]+>\s*)?(?:fun\s+)?(?:[\w<>\[\],.? ]+\s+)?([a-zA-Z_]\w*)\s*\(")
        imp = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)")
        for i, line in enumerate(lines, 1):
            m = imp.match(line)
            if m:
                symbols["imports"].append(m.group(1))
                continue
            m = cls.match(line)
            if m:
                symbols["classes"].append({"name": m.group(1), "line": i, "methods": []})
                continue
            m = method.match(line)
            if m and m.group(1) not in ("if", "for", "while", "switch", "catch", "return", "new"):
                symbols["functions"].append({"name": m.group(1), "line": i, "args": []})
        return symbols

    @staticmethod
    def _index_generic(file_path: Path) -> Dict[str, Any]:
        return {
            "classes": [],
            "functions": [],
            "imports": []
        }
