import os
from pathlib import Path
from app import config

WORKSPACE_ROOT = Path("/Users/saiganeshongolu/sAI").resolve()
CURRENT_WORKSPACE = WORKSPACE_ROOT

import threading

CURRENT_ACTIVITY_LOCK = threading.Lock()
CURRENT_ACTIVITY = {
    "status": "idle",
    "agent": "",
    "tool": "",
    "path": "",
    "command": "",
    "tasks": []
}

def get_current_activity() -> dict:
    with CURRENT_ACTIVITY_LOCK:
        return dict(CURRENT_ACTIVITY)

def update_current_activity(updates: dict):
    with CURRENT_ACTIVITY_LOCK:
        CURRENT_ACTIVITY.update(updates)

SHOULD_HALT = False

def trigger_halt():
    global SHOULD_HALT
    SHOULD_HALT = True

def check_and_reset_halt() -> bool:
    global SHOULD_HALT
    if SHOULD_HALT:
        SHOULD_HALT = False
        return True
    return False

def set_current_workspace(path_str: str):
    global CURRENT_WORKSPACE
    try:
        if path_str:
            resolved = Path(path_str).resolve()
            CURRENT_WORKSPACE = resolved
    except Exception:
        pass

def get_current_workspace() -> Path:
    global CURRENT_WORKSPACE
    return CURRENT_WORKSPACE

# Dynamic memory list of paths temporarily approved by the user
APPROVED_PATHS = set()
APPROVED_COMMANDS = set()

PRE_PATCH_BUFFERS = {}
POST_PATCH_BUFFERS = {}
COMMANDS_LOCK = threading.Lock()

import sqlite3
import datetime

TRANSACTIONS_DB = Path("/Users/saiganeshongolu/sAI/.sai/transactions.db")

def init_transactions_db():
    if not TRANSACTIONS_DB.parent.exists():
        TRANSACTIONS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(TRANSACTIONS_DB))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            path TEXT,
            before_content TEXT,
            after_content TEXT,
            session_id TEXT
        )
    """)
    conn.commit()
    conn.close()

init_transactions_db()

def save_transaction_snapshot(session_id: str, path: str, before_content: str, after_content: str):
    conn = sqlite3.connect(str(TRANSACTIONS_DB))
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO file_transactions (timestamp, path, before_content, after_content, session_id) VALUES (?, ?, ?, ?, ?)",
        (datetime.datetime.now().isoformat(), path, before_content, after_content, session_id)
    )
    conn.commit()
    conn.close()

def get_transaction_history(session_id: str) -> list:
    conn = sqlite3.connect(str(TRANSACTIONS_DB))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, timestamp, path, before_content, after_content FROM file_transactions WHERE session_id = ? ORDER BY id DESC LIMIT 20",
        (session_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        results.append({
            "id": r[0],
            "timestamp": r[1],
            "path": r[2],
            "before_content": r[3],
            "after_content": r[4]
        })
    return results

def revert_transaction(transaction_id: int) -> tuple:
    conn = sqlite3.connect(str(TRANSACTIONS_DB))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT path, before_content FROM file_transactions WHERE id = ?",
        (transaction_id,)
    )
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return False, "Transaction not found."
        
    path_str, before_content = row
    try:
        target_path = Path(path_str)
        if before_content is None:
            if target_path.exists():
                target_path.unlink()
        else:
            target_path.write_text(before_content, encoding="utf-8")
        return True, path_str
    except Exception as e:
        return False, str(e)

def approve_command(command: str):
    with COMMANDS_LOCK:
        APPROVED_COMMANDS.add(command.strip())

def is_command_approved(command: str) -> bool:
    with COMMANDS_LOCK:
        return command.strip() in APPROVED_COMMANDS

def consume_approved_command(command: str) -> bool:
    cmd_str = command.strip()
    with COMMANDS_LOCK:
        if cmd_str in APPROVED_COMMANDS:
            APPROVED_COMMANDS.remove(cmd_str)
            return True
    return False

def approve_path(path_str: str):
    # Check if it looks like a terminal command instead of a file path
    cmd_prefixes = ["rm ", "git ", "npm ", "docker ", "deploy", "delete", "cargo ", "go ", "pip "]
    if any(path_str.strip().startswith(prefix) for prefix in cmd_prefixes):
        approve_command(path_str)
        return
    try:
        resolved = Path(path_str).resolve()
        APPROVED_PATHS.add(resolved)
    except Exception:
        pass

def is_path_approved(target_path: Path) -> bool:
    try:
        resolved_target = target_path.resolve()
        for approved in APPROVED_PATHS:
            # Check if target is a subdirectory/subfile of the approved path
            if resolved_target.parts[:len(approved.parts)] == approved.parts:
                return True
    except Exception:
        pass
    return False

def validate_path(target_path: str | Path) -> bool:
    """
    Validates that a file path is located strictly within the active project workspace sandbox
    OR has been temporarily approved by the user.
    """
    try:
        path_obj = Path(target_path)
        resolved_target = path_obj.resolve()
        
        # 1. Check active workspace root
        active_ws = get_current_workspace()
        if resolved_target.parts[:len(active_ws.parts)] == active_ws.parts:
            return True

        # Check default sandbox root
        if resolved_target.parts[:len(WORKSPACE_ROOT.parts)] == WORKSPACE_ROOT.parts:
            return True
            
        # 2. Check approved paths
        return is_path_approved(resolved_target)
    except Exception:
        return False

def mask_secrets(text: str) -> str:
    """
    Masks any occurrences of sensitive API keys or environment secrets in logs,
    snapshots, or command outputs.
    """
    if not text:
        return text

    # Mask NVIDIA API key if it's set
    nv_key = config.NVIDIA_API_KEY
    if nv_key and len(nv_key) > 5 and nv_key != "your_nvidia_api_key_here":
        text = text.replace(nv_key, "[MASKED_NVIDIA_API_KEY]")
        # Also mask parts of it if it appears in parsed lists
        if nv_key in text:
             text = text.replace(nv_key, "[MASKED_NVIDIA_API_KEY]")

    return text
