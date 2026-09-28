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
    scripts_before = set(security.APPROVED_SCRIPTS)
    try:
        try:
            tool.execute({"script_path": "risky.py"})
            assert False, "expected PermissionRequestRequired"
        except PermissionRequestRequired as preq:
            assert preq.kind == "script"
            # Absolute, so approval doesn't depend on the approver's cwd.
            assert preq.path == str(script.resolve())
            security.approve_request(preq.path, preq.kind)

        # Approved -- the same script now runs without raising.
        result = tool.execute({"script_path": "risky.py"})
        assert "hi" in result

        # Editing the script after approval must re-require approval.
        script.write_text("import os\nos.system('echo changed')\n", encoding="utf-8")
        try:
            tool.execute({"script_path": "risky.py"})
            assert False, "expected PermissionRequestRequired after the script changed"
        except PermissionRequestRequired:
            pass
    finally:
        security.APPROVED_SCRIPTS.clear()
        security.APPROVED_SCRIPTS.update(scripts_before)
