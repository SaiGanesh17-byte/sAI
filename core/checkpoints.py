"""
/undo: roll back the file edits a turn made, using the before/after snapshots
that write_file / edit_file / patch_file already record in .sai/transactions.db.

A checkpoint is just the highest transaction id when a turn starts; undoing a
turn restores every file it touched to its content at that checkpoint. A file
you changed yourself afterwards (its content no longer matches what sAI wrote)
is skipped, never overwritten. Shell commands' side effects are not tracked.
"""
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from core.security import TRANSACTIONS_DB


@dataclass
class FileRestore:
    path: str
    before: Optional[str]      # None = the file didn't exist before the turn
    expected_now: Optional[str]  # what sAI last wrote
    conflict: bool = False     # changed since sAI wrote it -> skip


@dataclass
class UndoPlan:
    since_id: int
    restores: List[FileRestore] = field(default_factory=list)
    tx_ids: List[int] = field(default_factory=list)

    @property
    def safe(self) -> List[FileRestore]:
        return [r for r in self.restores if not r.conflict]

    @property
    def conflicts(self) -> List[FileRestore]:
        return [r for r in self.restores if r.conflict]


def _connect(db: Optional[Path]):
    return sqlite3.connect(str(db or TRANSACTIONS_DB))


def checkpoint(db: Optional[Path] = None) -> int:
    """Marks 'now': the highest transaction id so far (0 if none)."""
    with _connect(db) as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM file_transactions").fetchone()
    return int(row[0])


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else None
    except OSError:
        return None


def plan_undo(session_id: str, since_id: int, db: Optional[Path] = None) -> UndoPlan:
    with _connect(db) as conn:
        rows = conn.execute(
            "SELECT id, path, before_content, after_content FROM file_transactions "
            "WHERE session_id = ? AND id > ? ORDER BY id ASC",
            (session_id, since_id),
        ).fetchall()
    plan = UndoPlan(since_id=since_id, tx_ids=[r[0] for r in rows])
    by_path = {}
    for _, path, before, after in rows:
        if path not in by_path:
            by_path[path] = FileRestore(path=path, before=before, expected_now=after)
        else:
            by_path[path].expected_now = after  # the earliest 'before' wins, the latest 'after'
    for restore in by_path.values():
        restore.conflict = _read(Path(restore.path)) != restore.expected_now
        plan.restores.append(restore)
    return plan


def apply_undo(plan: UndoPlan, db: Optional[Path] = None) -> List[str]:
    """Restores the non-conflicting files; returns the paths restored."""
    restored = []
    for r in plan.safe:
        target = Path(r.path)
        if r.before is None:
            if target.exists():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(r.before, encoding="utf-8")
        restored.append(r.path)
    if plan.tx_ids:
        with _connect(db) as conn:
            conn.executemany("DELETE FROM file_transactions WHERE id = ?", [(i,) for i in plan.tx_ids])
    return restored
