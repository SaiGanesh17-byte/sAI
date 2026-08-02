import ast
from pathlib import Path
from typing import Dict, List, Any

class RepositoryIndexer:
    @staticmethod
    def index_file(file_path: Path) -> Dict[str, Any]:
        """
        Extract classes, functions, and symbols from a code file.
        Focuses on Python AST indexing with a simple fallback.
        """
        if file_path.suffix == '.py':
            return RepositoryIndexer._index_python(file_path)
        else:
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
                    if node.module:
                        symbols["imports"].append(node.module)
        except Exception:
            pass
        return symbols

    @staticmethod
    def _index_generic(file_path: Path) -> Dict[str, Any]:
        return {
            "classes": [],
            "functions": [],
            "imports": []
        }
