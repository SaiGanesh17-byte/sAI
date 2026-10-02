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
                if not self.ignore_parser.is_ignored(file_path):
                    files.append(file_path)

        if self._repo and files:
            # One `git check-ignore` for all paths. Asking per file spawned a git
            # process for each one: ~5.7s for a 165-file repo on every launch.
            try:
                import subprocess
                rels = [str(f.relative_to(self.workspace_path)) for f in files]
                out = subprocess.run(["git", "-C", str(self.workspace_path), "check-ignore", "--stdin"],
                                     input="\n".join(rels), capture_output=True, text=True, timeout=30)
                ignored = set(out.stdout.splitlines())  # exit 1 = nothing ignored; still fine
                files = [f for f, rel in zip(files, rels) if rel not in ignored]
            except Exception:
                pass
        return files
