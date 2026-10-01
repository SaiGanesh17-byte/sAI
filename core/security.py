import hashlib
import os
import re
from pathlib import Path
from typing import Optional
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

# Roots allowed in addition to the current workspace. Historically sAI's own
# folder was always writable (the web UI still relies on that); a front end
# that picks a real project workspace passes exclusive=True so agents working
# in that project can't also edit sAI itself.
_EXTRA_ALLOWED_ROOTS = [WORKSPACE_ROOT]


def set_current_workspace(path_str: str, exclusive: bool = False):
    global CURRENT_WORKSPACE
    try:
        if path_str:
            resolved = Path(path_str).resolve()
            CURRENT_WORKSPACE = resolved
            if exclusive:
                _EXTRA_ALLOWED_ROOTS.clear()
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

def revert_to_transaction_snapshot(session_id: str, target_tx_id: int) -> tuple:
    conn = sqlite3.connect(str(TRANSACTIONS_DB))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, path, before_content FROM file_transactions WHERE session_id = ? AND id >= ? ORDER BY id DESC",
        (session_id, target_tx_id)
    )
    rows = cursor.fetchall()
    
    if not rows:
        conn.close()
        return False, "No transactions found to revert."
        
    reverted_paths = []
    errors = []
    for tx_id, path_str, before_content in rows:
        try:
            target_path = Path(path_str)
            if before_content is None:
                if target_path.exists():
                    target_path.unlink()
            else:
                target_path.write_text(before_content, encoding="utf-8")
            reverted_paths.append(path_str)
        except Exception as e:
            errors.append(f"Failed to revert {path_str}: {e}")
            
    cursor.execute(
        "DELETE FROM file_transactions WHERE session_id = ? AND id >= ?",
        (session_id, target_tx_id)
    )
    conn.commit()
    conn.close()
    
    if errors:
        return False, "; ".join(errors)
    return True, f"Reverted {len(reverted_paths)} edits."

# Centralized risky-content detection, shared by any tool that runs shell
# commands or executes script content (TerminalTool, PythonTool). This is a
# pattern-matching heuristic, not a claim of airtight command-injection
# defense -- real isolation is the optional docker_sandbox setting. The goal
# here is consistent coverage across tools instead of a narrower, duplicated,
# shell-only list.
#
# (label, regex) pairs, matched against lowercased text. Word boundaries and
# \s (not a literal space) matter: plain substrings both missed "rm<TAB>-rf"
# and falsely flagged "terraform apply" / "perform x" as containing "rm ".
RISKY_PATTERNS = [
    # shell
    ("rm ", r"\brm\s"),
    ("sudo ", r"\bsudo\s"),
    ("chmod 777", r"\bchmod\s+(-r\s+)?777\b"),
    ("mkfs", r"\bmkfs\b"),
    ("dd if=", r"\bdd\s+if="),
    ("shutdown", r"\bshutdown\b"),
    ("reboot", r"\breboot\b"),
    ("kill -9", r"\bkill\s+-9\b"),
    ("> /dev/", r">\s*/dev/(?!null\b)"),
    (":(){ :|:& };:", r":\(\)\s*\{\s*:\|:&\s*\};:"),
    ("git push", r"\bgit\s+push\b"),
    ("git clean", r"\bgit\s+clean\b"),
    ("npm publish", r"\bnpm\s+publish\b"),
    ("docker run", r"\bdocker\s+run\b"),
    ("deploy", r"\bdeploy\b"),
    ("delete", r"\bdelete\b"),
    # pipe-to-shell
    ("| sh", r"\|\s*(sudo\s+)?(ba|z)?sh\b"),
    # python-script content
    ("os.system", r"\bos\.system\b"),
    ("os.popen", r"\bos\.popen\b"),
    ("subprocess", r"\bsubprocess\.(run|call|popen|check_call|check_output|getoutput)\b"),
    ("import subprocess", r"\bimport\s+subprocess\b|\bfrom\s+subprocess\s+import\b"),
    ("from os import", r"\bfrom\s+os\s+import\b"),
    ("__import__", r"\b__import__\b"),
    ("shutil.rmtree", r"\bshutil\.rmtree\b"),
    ("os.remove", r"\bos\.(remove|unlink|rmdir|removedirs)\b"),
]
_RISKY_REGEXES = [(label, re.compile(rx)) for label, rx in RISKY_PATTERNS]

def find_risky_pattern(text: str) -> Optional[str]:
    """
    Returns the label of the first RISKY_PATTERNS entry found in `text`
    (case-insensitive), or None if nothing matched.
    """
    lowered = (text or "").lower()
    for label, regex in _RISKY_REGEXES:
        if regex.search(lowered):
            return label
    return None

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

APPROVED_SCRIPTS = set()  # {(resolved_path, sha256_of_content)}

# ---------------------------------------------------------------------------
# Allow rules (Claude Code's "don't ask again"). Session rules come from
# answering "a" at a prompt; persistent ones live in settings.json
# ("allow_commands": ["pytest", "npm test", ...]).
# ---------------------------------------------------------------------------
SESSION_ALLOW_COMMAND_PREFIXES = set()
_SESSION_STATE = {"auto_edits": False}

# A rule for "pytest" must never approve "pytest && rm -rf ~".
_SHELL_OPERATORS = re.compile(r"[;&|`<>\n]|\$\(")


def command_allow_prefix(command: str) -> str:
    """The prefix an "always allow" answer covers: the program plus its
    subcommand when there is one ("git push origin main" -> "git push")."""
    tokens = (command or "").split()
    if len(tokens) >= 2 and not tokens[1].startswith("-"):
        return " ".join(tokens[:2])
    return tokens[0] if tokens else ""


def allow_command_prefix_for_session(prefix: str):
    if prefix.strip():
        SESSION_ALLOW_COMMAND_PREFIXES.add(prefix.strip())


def is_command_allowed_by_rule(command: str) -> bool:
    if not command or _SHELL_OPERATORS.search(command):
        return False
    from core.settings import load_settings
    try:
        persistent = load_settings().get("allow_commands") or []
    except Exception:
        persistent = []
    tokens = command.split()
    for rule in set(persistent) | SESSION_ALLOW_COMMAND_PREFIXES:
        rule_tokens = str(rule).split()
        if rule_tokens and tokens[:len(rule_tokens)] == rule_tokens:
            return True
    return False


APPROVED_MCP_TOOLS = set()          # one-shot approvals
SESSION_ALLOWED_MCP_TOOLS = set()   # "a" at the prompt


def consume_approved_mcp_tool(name: str) -> bool:
    with COMMANDS_LOCK:
        if name in APPROVED_MCP_TOOLS:
            APPROVED_MCP_TOOLS.discard(name)
            return True
    return False


def allow_mcp_tool_for_session(name: str):
    SESSION_ALLOWED_MCP_TOOLS.add(name)


def is_mcp_tool_allowed(name: str) -> bool:
    """settings allow_mcp_tools entries match exactly, as a prefix ending in '__'
    ("mcp__github__" = every tool from that server), or as a glob ("mcp__github__list_*")."""
    if name in SESSION_ALLOWED_MCP_TOOLS:
        return True
    from core.settings import load_settings
    try:
        rules = load_settings().get("allow_mcp_tools") or []
    except Exception:
        rules = []
    import fnmatch
    return any(name == r or (str(r).endswith("__") and name.startswith(str(r))) or fnmatch.fnmatch(name, str(r))
               for r in rules)


def set_session_auto_edits(enabled: bool):
    _SESSION_STATE["auto_edits"] = bool(enabled)


def edits_need_approval() -> bool:
    """Edits are asked about unless auto-approved this session or by setting
    ("edit_approval": "auto" -- Claude Code's acceptEdits mode)."""
    if _SESSION_STATE["auto_edits"]:
        return False
    from core.settings import load_settings
    try:
        return load_settings().get("edit_approval", "ask") != "auto"
    except Exception:
        return True

def _file_sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None

def approve_script(path_str: str):
    """
    Approves a script for its content *as of now*. If the file is edited after
    approval (e.g. by an agent), is_script_approved() returns False again, so
    approving a benign-looking script can't be reused to run a rewritten one.
    """
    resolved = Path(path_str).resolve()
    digest = _file_sha256(resolved)
    if digest:
        APPROVED_SCRIPTS.add((resolved, digest))

def is_script_approved(path: Path) -> bool:
    resolved = path.resolve()
    digest = _file_sha256(resolved)
    return digest is not None and (resolved, digest) in APPROVED_SCRIPTS

def approve_request(path_str: str, kind: Optional[str] = None):
    """
    Approves a PermissionRequestRequired by its declared kind. Callers should
    use this (passing preq.kind) rather than approve_path(), whose
    prefix-guessing misrouted e.g. "sudo ls" into APPROVED_PATHS (so the
    approved command was re-blocked forever) and "deploy/x.yaml" into
    APPROVED_COMMANDS.
    """
    if kind == "command":
        approve_command(path_str)
    elif kind == "script":
        approve_script(path_str)
    elif kind == "mcp":
        with COMMANDS_LOCK:
            APPROVED_MCP_TOOLS.add(path_str)
    elif kind == "path":
        try:
            APPROVED_PATHS.add(Path(path_str).resolve())
        except Exception:
            pass
    else:
        approve_path(path_str)

def approve_path(path_str: str):
    # Legacy entry point for callers that don't know the request kind.
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

        # Additional allowed roots (sAI's own folder unless a front end opted out)
        for root in _EXTRA_ALLOWED_ROOTS:
            if resolved_target.parts[:len(root.parts)] == root.parts:
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

    # Read keys at call time: core/settings.py injects them into os.environ
    # after startup (from Keychain), so an import-time snapshot would miss them.
    for env_name in ("NVIDIA_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        key = os.environ.get(env_name, "")
        if key and len(key) > 5 and key != "your_nvidia_api_key_here":
            text = text.replace(key, f"[MASKED_{env_name}]")

    return text
