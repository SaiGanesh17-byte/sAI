from core import security
from execution.permissions import PermissionRequestRequired
from tools.python import PythonTool


def test_run_benign_script_succeeds(tmp_workspace, monkeypatch):
    monkeypatch.setattr("tools.python.WORKSPACE_ROOT", tmp_workspace.resolve())
    script = tmp_workspace / "benign.py"
    script.write_text("print('hello from script')\n", encoding="utf-8")

    tool = PythonTool()
    result = tool.execute({"script_path": "benign.py"})
    assert "hello from script" in result


def test_run_risky_script_requires_approval(tmp_workspace, monkeypatch):
    monkeypatch.setattr("tools.python.WORKSPACE_ROOT", tmp_workspace.resolve())
    script = tmp_workspace / "risky.py"
    script.write_text("import os\nos.system('echo hi')\n", encoding="utf-8")

    tool = PythonTool()
    paths_before = set(security.APPROVED_PATHS)
    try:
        try:
            tool.execute({"script_path": "risky.py"})
            assert False, "expected PermissionRequestRequired"
        except PermissionRequestRequired:
            pass

        # Approve it, then the same script should run without raising.
        security.approve_path(str((tmp_workspace / "risky.py").resolve()))
        result = tool.execute({"script_path": "risky.py"})
        assert "hi" in result
    finally:
        security.APPROVED_PATHS.clear()
        security.APPROVED_PATHS.update(paths_before)
