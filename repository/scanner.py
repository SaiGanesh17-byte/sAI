import os
from pathlib import Path
from typing import List
from git import Repo
from repository.ignore import IgnoreParser

class RepositoryScanner:
    def __init__(self, workspace_path: str | Path):
        self.workspace_path = Path(workspace_path).resolve()
        self.ignore_parser = IgnoreParser(self.workspace_path)
        self._repo = None
        try:
            self._repo = Repo(self.workspace_path)
        except Exception:
            pass

    def scan(self) -> List[Path]:
        """
        Scan workspace root and return list of all files honoring gitignore rules.
        """
        files = []
        for root, dirs, filenames in os.walk(self.workspace_path):
            dirs[:] = [d for d in dirs if not self.ignore_parser.is_ignored(Path(root) / d)]
            
            for f in filenames:
                file_path = Path(root) / f
                if self.ignore_parser.is_ignored(file_path):
                    continue
                
                if self._repo:
                    try:
                        rel_path = file_path.relative_to(self.workspace_path)
                        if self._repo.ignored(str(rel_path)):
                            continue
                    except Exception:
                        pass
                
                files.append(file_path)
        return files
