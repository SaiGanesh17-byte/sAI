from typing import Dict, List, Set, Any

class SymbolGraph:
    def __init__(self):
        self.definitions: Dict[str, Dict[str, Any]] = {}
        self.imports: Dict[str, Set[str]] = {}

    def clear(self) -> None:
        self.definitions.clear()
        self.imports.clear()

    def add_definition(self, name: str, file_path: str, kind: str, meta: Dict[str, Any]) -> None:
        self.definitions[name] = {
            "file": file_path,
            "kind": kind,
            "meta": meta
        }

    def add_imports(self, file_path: str, imports_list: List[str]) -> None:
        if file_path not in self.imports:
            self.imports[file_path] = set()
        self.imports[file_path].update(imports_list)

    def get_defining_file(self, symbol_name: str) -> str | None:
        defn = self.definitions.get(symbol_name)
        return defn["file"] if defn else None
