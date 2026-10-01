import time
from types import SimpleNamespace

import pytest

from agents.loop import execute_with_approval
from core.kernel import kernel
from execution.engine import ExecutionEngine
from tools.filesystem import EditFileTool, ReadFileTool, apply_exact_edit
from tools.find import GlobTool, GrepTool
from tools.registry import ToolRegistry
from tools.terminal import BashOutputTool, KillShellTool, TerminalTool, background_shells
from tools.todo import TodoWriteTool, get_todos, set_todos
from tools.web_fetch import WebFetchTool, html_to_text


@pytest.fixture
def project(tmp_workspace):
    (tmp_workspace / "src").mkdir()
    (tmp_workspace / "src" / "app.py").write_text("def main():\n    return 'hi'\n\nTODO = 1\n")
    (tmp_workspace / "src" / "util.py").write_text("def helper():\n    pass  # TODO later\n")
    (tmp_workspace / "README.md").write_text("# demo\n")
    (tmp_workspace / "node_modules").mkdir()
    (tmp_workspace / "node_modules" / "dep.py").write_text("TODO = 'ignored'\n")
    return tmp_workspace


# --- glob / grep ----------------------------------------------------------------

def test_glob_matches_nested_and_top_level(project):
    out = GlobTool().execute({"pattern": "**/*.py"}).splitlines()
    assert sorted(out) == ["src/app.py", "src/util.py"]  # node_modules skipped
    assert GlobTool().execute({"pattern": "**/*.md"}) == "README.md"


def test_grep_modes(project):
    assert sorted(GrepTool().execute({"pattern": "TODO"}).splitlines()) == ["src/app.py", "src/util.py"]
    content = GrepTool().execute({"pattern": "def \\w+", "output_mode": "content", "glob": "*.py"})
    assert "src/app.py:1: def main():" in content and "src/util.py:1: def helper():" in content
    assert "src/app.py: 1" in GrepTool().execute({"pattern": "todo", "case_insensitive": True, "output_mode": "count"})


def test_grep_bad_regex_and_outside_path(project):
    assert GrepTool().execute({"pattern": "("}).startswith("Error: invalid regular expression")
    assert "outside the workspace" in GrepTool().execute({"pattern": "x", "path": "/etc"})


# --- edit_file --------------------------------------------------------------------

def test_apply_exact_edit_rules():
    assert apply_exact_edit("a b a", "b", "c") == ("a c a", None)
    assert "appears 2 times" in apply_exact_edit("a b a", "a", "z")[1]
    assert apply_exact_edit("a b a", "a", "z", replace_all=True) == ("z b z", None)
    assert "not found" in apply_exact_edit("abc", "x", "y")[1]


@pytest.fixture
def edit_tools():
    had, prev = "tool_registry" in kernel._services, kernel._services.get("tool_registry")
    reg = ToolRegistry()
    reg.register(ReadFileTool())
    reg.register(EditFileTool())
    kernel.register_service("tool_registry", reg)
    yield
    if had:
        kernel.register_service("tool_registry", prev)
    else:
        kernel._services.pop("tool_registry", None)


def test_edit_file_goes_through_read_rule_and_diff_approval(project, edit_tools):
    engine = ExecutionEngine()
    action = {"tool": "edit_file", "args": {"path": "src/app.py", "old_string": "return 'hi'", "new_string": "return 'hello'"}}

    unread = engine.execute(action)
    assert not unread.success and "haven't read it" in unread.stderr

    engine.execute({"tool": "read_file", "args": {"path": "src/app.py"}})
    seen = []
    outcome = execute_with_approval(engine, action, lambda preq, a: seen.append(preq) or True)
    assert outcome.status == "ok"
    assert "-    return 'hi'" in seen[0].details and "+    return 'hello'" in seen[0].details
    assert "return 'hello'" in (project / "src" / "app.py").read_text()


# --- todo_write ---------------------------------------------------------------------

def test_todo_write_validates_and_renders():
    set_todos([])
    tool = TodoWriteTool()
    out = tool.execute({"todos": [{"content": "write api", "status": "completed"},
                                  {"content": "add tests", "status": "in_progress"},
                                  {"content": "docs", "status": "pending"}]})
    assert out == "☒ write api\n◼ add tests\n☐ docs"
    assert [t["status"] for t in get_todos()] == ["completed", "in_progress", "pending"]
    assert "only one" in tool.execute({"todos": [{"content": "a", "status": "in_progress"}, {"content": "b", "status": "in_progress"}]})
    assert "invalid status" in tool.execute({"todos": [{"content": "a", "status": "done"}]})
    set_todos([])


def test_todos_appear_in_agent_prompt(tmp_workspace):
    from core.prompt_builder import PromptBuilder
    from core.task import Task
    set_todos([{"content": "migrate db", "status": "in_progress"}])
    try:
        ctx = Task(goal="x").context
        prompt = PromptBuilder.build(ctx, None, ctx.memory, ctx.conversation)
        assert "[CURRENT TODO LIST" in prompt and "◼ migrate db" in prompt
    finally:
        set_todos([])


# --- web_fetch ----------------------------------------------------------------------

def test_html_to_text_keeps_main_content():
    html = "<html><head><title>Docs</title><script>x()</script></head><body><nav>menu</nav><main><h1>Install</h1><p>pip install sai</p></main></body></html>"
    text = html_to_text(html)
    assert text.startswith("# Docs") and "pip install sai" in text
    assert "menu" not in text and "x()" not in text


def test_web_fetch_rejects_non_http():
    assert WebFetchTool().execute({"url": "file:///etc/passwd"}).startswith("Error")


def _fake_dns(monkeypatch, table):
    """table: hostname -> IP the fake resolver returns."""
    import socket
    from tools import web_fetch

    def getaddrinfo(host, port, *a, **k):
        if host not in table:
            raise socket.gaierror("unknown host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], port))]

    monkeypatch.setattr(web_fetch.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr("core.settings.load_settings", lambda: {"web_fetch_allow_private": False})


def _response(status=200, ctype="text/html", text="", location=None):
    headers = {"content-type": ctype}
    if location:
        headers["location"] = location
    return SimpleNamespace(status_code=status, headers=headers, text=text)


def test_web_fetch_converts_html(monkeypatch):
    import httpx
    _fake_dns(monkeypatch, {"x.dev": "93.184.215.14"})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _response(text="<title>T</title><main>hello</main>"))
    out = WebFetchTool().execute({"url": "https://x.dev/a"})
    assert out.startswith("URL: https://x.dev/a") and "hello" in out


@pytest.mark.parametrize("url,ip", [
    ("http://localhost:8000/admin", "127.0.0.1"),
    ("http://169.254.169.254/latest/meta-data/", "169.254.169.254"),  # cloud metadata
    ("http://router.lan/", "192.168.1.1"),
    ("http://internal.corp/", "10.0.0.5"),
    ("http://ten.example/", "100.64.0.1"),  # carrier-grade NAT, also not global
])
def test_web_fetch_blocks_internal_addresses(monkeypatch, url, ip):
    import httpx
    from urllib.parse import urlparse
    _fake_dns(monkeypatch, {urlparse(url).hostname: ip})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not connect")))
    out = WebFetchTool().execute({"url": url})
    assert out.startswith("Error: refusing to fetch") and "private/internal" in out


def test_web_fetch_rechecks_every_redirect_hop(monkeypatch):
    import httpx
    _fake_dns(monkeypatch, {"public.dev": "93.184.215.14", "localhost": "127.0.0.1"})
    calls = []

    def get(url, **k):
        calls.append(url)
        return _response(status=302, location="http://localhost:6379/")

    monkeypatch.setattr(httpx, "get", get)
    out = WebFetchTool().execute({"url": "https://public.dev/go"})
    assert calls == ["https://public.dev/go"]  # the internal hop was never requested
    assert "refusing to fetch http://localhost:6379/" in out


def test_web_fetch_follows_safe_redirects(monkeypatch):
    import httpx
    _fake_dns(monkeypatch, {"a.dev": "93.184.215.14", "b.dev": "93.184.215.15"})
    responses = iter([_response(status=301, location="https://b.dev/final"), _response(text="<main>moved here</main>")])
    monkeypatch.setattr(httpx, "get", lambda *a, **k: next(responses))
    out = WebFetchTool().execute({"url": "https://a.dev/start"})
    assert out.startswith("URL: https://b.dev/final (redirected from https://a.dev/start)") and "moved here" in out


def test_web_fetch_private_allowed_by_setting(monkeypatch):
    import httpx
    _fake_dns(monkeypatch, {"localhost": "127.0.0.1"})
    monkeypatch.setattr("core.settings.load_settings", lambda: {"web_fetch_allow_private": True})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _response(ctype="text/plain", text="local docs"))
    assert "local docs" in WebFetchTool().execute({"url": "http://localhost:8000/docs"})


# --- background shells -----------------------------------------------------------------

def test_background_shell_lifecycle(tmp_workspace):
    msg = TerminalTool().execute({"command": "echo started; sleep 30", "run_in_background": True})
    shell_id = msg.split("'")[1]
    for _ in range(50):
        out = BashOutputTool().execute({"shell_id": shell_id})
        if "started" in out:
            break
        time.sleep(0.05)
    assert out.startswith("[running]") and "started" in out
    assert BashOutputTool().execute({"shell_id": shell_id}) == "[running]\n(no new output)"  # only new output
    assert "Stopped" in KillShellTool().execute({"shell_id": shell_id})
    time.sleep(0.1)
    assert "exited" in BashOutputTool().execute({"shell_id": shell_id})
    assert BashOutputTool().execute({"shell_id": "nope"}).startswith("Error")


def test_background_commands_get_the_same_safety_checks(tmp_workspace):
    from execution.permissions import PermissionRequestRequired
    with pytest.raises(PermissionRequestRequired):
        TerminalTool().execute({"command": "sudo sleep 1", "run_in_background": True})
    assert TerminalTool().execute({"command": "cat ~/.zshrc", "run_in_background": True}).startswith("Security Error")
