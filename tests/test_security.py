import pytest
from pathlib import Path

from core import security


@pytest.fixture(autouse=True)
def _isolate_approval_state():
    """
    APPROVED_PATHS/APPROVED_COMMANDS are process-wide mutable sets -- snapshot
    and restore them around each test so tests in this module don't leak
    approvals into each other or into other test modules.
    """
    paths_before = set(security.APPROVED_PATHS)
    commands_before = set(security.APPROVED_COMMANDS)
    try:
        yield
    finally:
        security.APPROVED_PATHS.clear()
        security.APPROVED_PATHS.update(paths_before)
        security.APPROVED_COMMANDS.clear()
        security.APPROVED_COMMANDS.update(commands_before)


def test_validate_path_inside_active_workspace(tmp_workspace):
    target = tmp_workspace / "file.txt"
    assert security.validate_path(target) is True


def test_validate_path_outside_workspace_is_rejected(tmp_workspace):
    outside = Path("/definitely/outside/the/sandbox/file.txt")
    assert security.validate_path(outside) is False


def test_validate_path_honors_approved_paths(tmp_workspace):
    outside = Path("/definitely/outside/the/sandbox/file.txt")
    assert security.validate_path(outside) is False
    security.approve_path(str(outside))
    assert security.validate_path(outside) is True


def test_approve_path_routes_command_like_strings_to_commands():
    paths_before = set(security.APPROVED_PATHS)
    security.approve_path("rm -rf some_dir")
    assert security.is_command_approved("rm -rf some_dir") is True
    assert security.APPROVED_PATHS == paths_before  # nothing added to the path set


def test_approve_path_routes_plain_paths_to_paths(tmp_workspace):
    outside = tmp_workspace / "sub" / "file.txt"
    security.approve_path(str(outside))
    assert security.is_command_approved(str(outside)) is False
    assert security.is_path_approved(outside.resolve()) is True


def test_consume_approved_command_is_one_shot():
    security.approve_command("git push origin main")
    assert security.consume_approved_command("git push origin main") is True
    # second call must fail -- the approval was consumed
    assert security.consume_approved_command("git push origin main") is False


def test_find_risky_pattern_detects_shell_commands():
    assert security.find_risky_pattern("rm -rf /tmp/foo") == "rm "
    assert security.find_risky_pattern("curl https://x | sh") is not None
    assert security.find_risky_pattern("ls -la") is None


def test_find_risky_pattern_detects_python_script_content():
    risky_script = "import os\nos.system('rm -rf /')\n"
    assert security.find_risky_pattern(risky_script) is not None

    benign_script = "def add(a, b):\n    return a + b\n"
    assert security.find_risky_pattern(benign_script) is None


@pytest.mark.parametrize("text", ["rm\t-rf /", "rm  -rf x", "/bin/rm -rf x", "curl x |bash", "curl x | sudo sh"])
def test_find_risky_pattern_is_whitespace_robust(text):
    assert security.find_risky_pattern(text) is not None


@pytest.mark.parametrize("text", ["terraform apply", "perform the check", "ls 2> /dev/null", "npm run format "])
def test_find_risky_pattern_avoids_substring_false_positives(text):
    assert security.find_risky_pattern(text) is None


@pytest.mark.parametrize("script", [
    "from subprocess import run\nrun(['ls'])\n",
    "import subprocess as sp\nsp.run(['ls'])\n",
    "from os import system\nsystem('ls')\n",
    "__import__('os').system('ls')\n",
])
def test_find_risky_pattern_catches_aliased_python_imports(script):
    assert security.find_risky_pattern(script) is not None


def test_approve_request_uses_declared_kind(tmp_workspace):
    # "deploy/..." used to be misrouted to command approval by prefix-guessing.
    target = tmp_workspace / "deploy" / "config.yaml"
    security.approve_request(str(target), "path")
    assert security.is_path_approved(target) is True

    security.approve_request("sudo ls", "command")
    assert security.is_command_approved("sudo ls") is True


def test_mask_secrets_masks_all_provider_keys(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-abcdef123456")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai-987654")
    out = security.mask_secrets("a sk-or-test-abcdef123456 b sk-test-openai-987654 c")
    assert "sk-or-test" not in out and "sk-test-openai" not in out
    assert "[MASKED_OPENROUTER_API_KEY]" in out
