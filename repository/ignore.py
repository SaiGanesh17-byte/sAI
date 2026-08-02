import fnmatch
from pathlib import Path
from typing import List

class IgnoreParser:
    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path).resolve()
        self.patterns: List[str] = []
        self._load_patterns()

    def _load_patterns(self):
        # Default standard ignore patterns
        self.patterns.extend([
            ".git", "venv", "node_modules", "__pycache__", ".idea", ".vscode",
            ".gemini", ".agents", ".sai", ".DS_Store", "*.pyc", "*.pyo"
        ])
        
        # Load from local repository configuration ignore files
        ignore_files = [".gitignore", ".claudeignore", ".saiignore"]
        for f_name in ignore_files:
            ignore_file = self.workspace_path / f_name
            if ignore_file.exists():
                try:
                    lines = ignore_file.read_text(encoding="utf-8").splitlines()
                    for line in lines:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            # Handle leading or trailing slashes for standard glob matching
                            p = line.rstrip("/")
                            if p.startswith("/"):
                                p = p[1:]
                            self.patterns.append(p)
                except Exception:
                    pass

    def is_ignored(self, path: Path | str) -> bool:
        resolved_path = Path(path).resolve()
        try:
            rel_path = resolved_path.relative_to(self.workspace_path)
        except ValueError:
            return True
            
        rel_str = str(rel_path)
        parts = rel_path.parts
        
        for pattern in self.patterns:
            if fnmatch.fnmatch(rel_str, pattern) or fnmatch.fnmatch(rel_str, f"*/{pattern}"):
                return True
            for part in parts:
                if fnmatch.fnmatch(part, pattern):
                    return True
        return False
