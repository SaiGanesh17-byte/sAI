"""
Hooks: your own shell commands that run at fixed points, like Claude Code's.
Configured only in sAI's settings.json (never from project files, so a cloned
repo can't make commands run on its own):

    "hooks": {
      "PreToolUse":       [{"matcher": "edit_file|write_file", "command": "./scripts/check.sh"}],
      "PostToolUse":      [{"matcher": "edit_file|write_file", "command": "black -q \\"$SAI_FILE_PATH\\""}],
      "UserPromptSubmit": [{"command": "./scripts/add_context.sh"}],
      "Stop":             [{"command": "say 'sAI is done'"}]
    }

Each command gets the event as JSON on stdin and SAI_EVENT / SAI_TOOL /
SAI_FILE_PATH / SAI_WORKSPACE in its environment, runs in the workspace,
and is killed after `timeout` seconds (default 60).

Exit codes:
  0  -> fine. For UserPromptSubmit, stdout is added to the request as context.
  2  -> block. PreToolUse: the tool doesn't run and stderr goes back to the
        agent as the reason. UserPromptSubmit: the request is dropped and
        stderr is shown. PostToolUse: stderr is added to the tool result as
        feedback for the agent. (Stop is notify-only.)
  other -> a warning is shown; nothing is blocked.

`matcher` is a regex matched against the tool name (PreToolUse/PostToolUse);
empty or "*" matches every tool.
"""
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop")
DEFAULT_TIMEOUT = 60
BLOCK_EXIT_CODE = 2


@dataclass
class HookResult:
    blocked: bool = False
    reason: str = ""                      # stderr of the blocking hook
    context: str = ""                     # UserPromptSubmit stdout to add to the prompt
    warnings: List[str] = field(default_factory=list)


def _configured(event: str) -> List[dict]:
    from core.settings import load_settings
    try:
        hooks = (load_settings().get("hooks") or {}).get(event) or []
    except Exception:
        return []
    return [h for h in hooks if isinstance(h, dict) and str(h.get("command", "")).strip()]


def _matches(matcher: Optional[str], tool: Optional[str]) -> bool:
    if not matcher or matcher == "*" or tool is None:
        return True
    try:
        return re.fullmatch(matcher, tool) is not None
    except re.error:
        return matcher == tool


def run_hooks(event: str, payload: Dict[str, Any], tool: Optional[str] = None) -> HookResult:
    from core.security import get_current_workspace

    result = HookResult()
    hooks = [h for h in _configured(event) if _matches(h.get("matcher"), tool)]
    if not hooks:
        return result

    workspace = str(get_current_workspace())
    args = payload.get("args") or {}
    env = dict(os.environ, SAI_EVENT=event, SAI_TOOL=tool or "", SAI_WORKSPACE=workspace,
               SAI_FILE_PATH=str(args.get("path") or args.get("script_path") or ""))
    stdin = json.dumps({"event": event, "tool": tool, "workspace": workspace, **payload}, default=str)

    contexts = []
    for hook in hooks:
        command = str(hook["command"])
        try:
            # shell=True is deliberate: `command` is written by the user in their own
            # settings.json, like a shell alias. Agent-controlled data (tool args,
            # paths) is never interpolated into it -- it arrives only via stdin JSON
            # and SAI_* env vars, which hooks should quote ("$SAI_FILE_PATH").
            proc = subprocess.run(command, shell=True, cwd=workspace, env=env, input=stdin, text=True,
                                  capture_output=True, timeout=float(hook.get("timeout", DEFAULT_TIMEOUT)))
        except subprocess.TimeoutExpired:
            result.warnings.append(f"{event} hook timed out: {command}")
            continue
        except OSError as e:
            result.warnings.append(f"{event} hook failed to start ({e}): {command}")
            continue

        if proc.returncode == 0:
            if event == "UserPromptSubmit" and proc.stdout.strip():
                contexts.append(proc.stdout.strip())
        elif proc.returncode == BLOCK_EXIT_CODE and event != "Stop":
            result.blocked = True
            result.reason = (proc.stderr or proc.stdout).strip() or f"blocked by hook: {command}"
            break
        else:
            detail = (proc.stderr or proc.stdout).strip()[:300]
            result.warnings.append(f"{event} hook exited {proc.returncode}: {command}" + (f" -- {detail}" if detail else ""))

    result.context = "\n\n".join(contexts)
    return result
