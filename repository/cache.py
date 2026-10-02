import json
from pathlib import Path
from typing import Dict, Any

class RepositoryCache:
    def __init__(self, cache_file_path: str | Path):
        self.cache_file_path = Path(cache_file_path)
        self.data: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if self.cache_file_path.exists():
            try:
                self.data = json.loads(self.cache_file_path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}
        else:
            self.data = {}

    def save(self) -> None:
        try:
            self.cache_file_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file_path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get_file_cache(self, file_path_str: str) -> Dict[str, Any] | None:
        return self.data.get(file_path_str)

    def set_file_cache(self, file_path_str: str, mtime: float, symbols: Dict[str, Any]) -> None:
        from repository.indexer import INDEX_VERSION
        self.data[file_path_str] = {
            "mtime": mtime,
            "symbols": symbols,
            "v": INDEX_VERSION,
        }
