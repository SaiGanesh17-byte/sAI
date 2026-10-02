from pathlib import Path

import pytest

from agents.loop import ActionOutcome
from core.context_graph import ContextGraph


@pytest.fixture
def project(tmp_workspace):
    w = tmp_workspace
    (w / "app").mkdir()
    (w / "app" / "__init__.py").write_text("")
    (w / "app" / "store.py").write_text("import json\n\ndef save_settings(cfg):\n    return json.dumps(cfg)\n")
    (w / "app" / "cli.py").write_text("from .store import save_settings\n\ndef on_exit(cfg):\n    save_settings(cfg)\n")
    (w / "calc.py").write_text("def add(a, b):\n    return a - b\n\ndef divide(a, b):\n    return a / b\n")
    (w / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    (w / "web").mkdir()
    (w / "web" / "api.ts").write_text("export async function fetchUser(id) {}\n")
    (w / "web" / "page.tsx").write_text("import { fetchUser } from './api';\nexport default function Page() {}\n")
    (w / "README.md").write_text("# demo\n")
    g = ContextGraph(w)
    g.refresh(force=True)
    return w, g


def test_code_layer_resolves_imports_and_tests(project):
    _, g = project
    assert g.files["app/cli.py"].imports == {"app/store.py"}          # python relative import
    assert g.files["app/store.py"].imported_by == {"app/cli.py"}
    assert g.files["web/page.tsx"].imports == {"web/api.ts"}           # JS relative import
    assert "test_calc.py" in g.files["calc.py"].tests                  # by name and by import
    assert ("calc.py", 4, "function") in g.symbol_index["divide"]


def test_neighborhood_focuses_on_what_the_request_mentions(project):
    _, g = project
    hood = g.neighborhood("the test in test_calc.py fails, fix it")
    assert hood.startswith("test_calc.py:") or "test_calc.py:" in hood
    assert "calc.py: defines add() L1, divide() L4" in hood
    assert "save_settings" not in hood                                 # unrelated code stays out

    hood = g.neighborhood("rename save_settings to persist_settings")
    assert "save_settings: defined at app/store.py:3; referenced at app/cli.py:1, app/cli.py:4" in hood
    assert "imported by app/cli.py" in hood


def test_docs_are_not_pulled_in_by_ordinary_words(project):
    _, g = project
    assert "README.md" not in g.neighborhood("update the readme section about tools")


def test_work_layer_records_edits_and_errors_across_restarts(project):
    w, g = project
    g.note_request("fix the failing test")
    g.record_action({"tool": "read_file", "args": {"path": "calc.py"}}, ActionOutcome("ok", "def add..."), "Debugger")
    g.record_action({"tool": "execute_command", "args": {"command": "python3 -m pytest"}}, ActionOutcome("ok",
        'Traceback (most recent call last):\n  File "' + str(w / "test_calc.py") + '", line 4, in test_add\n'
        "    assert add(2, 3) == 5\nAssertionError"), "Debugger")
    g.record_action({"tool": "edit_file", "args": {"path": "calc.py"}}, ActionOutcome("ok", "Success"), "Debugger")

    hood = g.neighborhood("fix the failing test")
    assert "recent errors: test_calc.py:4" in hood and "AssertionError" in hood
    assert "changed recently: calc.py (edit, turn 1)" in hood

    reloaded = ContextGraph(w)                                         # new session, same project
    assert reloaded.turn == 1 and any(e.get("kind") == "error" for e in reloaded.activity)


def test_query_symbol_lists_definition_and_references(project):
    _, g = project
    out = g.query("save_settings")
    assert "defined in: app/store.py:3" in out
    assert "app/cli.py:4: save_settings(cfg)" in out
    assert "Nothing named" in g.query("nonexistent_thing")


def test_knowledge_graph_entities_are_included(project, monkeypatch):
    import json
    from memory.graphiti import workspace_memory_file
    w, g = project
    kg = workspace_memory_file(w).parent / "knowledge_graph.jsonl"
    kg.parent.mkdir(parents=True, exist_ok=True)
    kg.write_text(json.dumps({"type": "entity", "name": "calc module", "entityType": "component",
                              "observations": ["divide must raise ValueError on zero (decided 2026-10-02)"]}) + "\n")
    assert "project knowledge: calc module [component]: divide must raise ValueError" in g.neighborhood("change divide in calc.py")
