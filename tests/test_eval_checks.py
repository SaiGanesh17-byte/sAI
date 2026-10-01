import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
from run_evals import CASES_FILE, check  # noqa: E402


def _ok(result, **extra):
    return {"result": result, "route": "single_agent", "agent": "Coder", "is_error": False, **extra}


def test_meta_answer_is_caught():
    assert any("meta answer" in f for f in check({}, _ok("Providing general guidance on debugging Spring Batch."), Path("."), {}))
    assert check({}, _ok("Enable DEBUG logging for org.springframework.batch and set breakpoints in your reader."), Path("."), {}) == []


def test_content_checks():
    case = {"expect": {"contains_all": ["lsof"], "contains_any": ["kill", "stop"], "contains_any_2": ["8080"],
                       "not_contains": ["sudo rm"], "regex": r"lsof -i\s*:?8080", "min_length": 10}}
    assert check(case, _ok("Run `lsof -i :8080`, then kill the PID."), Path("."), {}) == []
    fails = check(case, _ok("Use netstat and sudo rm the pidfile."), Path("."), {})
    assert len(fails) == 5


def test_route_agent_and_error():
    case = {"expect": {"route_in": ["direct_answer"], "agent_in": ["Researcher"]}}
    assert len(check(case, _ok("A long enough real answer about something useful to the user."), Path("."), {})) == 2
    assert check(case, {"is_error": True, "error": "boom"}, Path("."), {}) == ["run errored: boom"]


def test_file_checks(tmp_path):
    (tmp_path / "calc.py").write_text("return a + b")
    (tmp_path / "keep.txt").write_text("changed!")
    case = {"expect": {"file_contains": {"calc.py": "a + b"}, "file_exists": ["calc.py"],
                       "file_missing": ["junk.py"], "file_unchanged": ["keep.txt", "gone.txt"]}}
    fails = check(case, _ok("Fixed calc.py so add() returns a + b; the test now passes."), tmp_path,
                  {"keep.txt": "original", "gone.txt": "x"})
    assert fails == ["keep.txt was modified", "gone.txt was deleted"]


def test_cases_file_is_well_formed():
    cases = yaml.safe_load(CASES_FILE.read_text())
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    assert {"developer", "tester", "prod-support", "business-analyst", "project-manager", "safety"} <= {c["persona"] for c in cases}
    for c in cases:
        assert c["prompt"].strip() and isinstance(c.get("expect", {}), dict)
