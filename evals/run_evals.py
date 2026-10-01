"""
Real-model evaluation for sAI: runs each case in evals/cases.yaml through
`sai -p` in a fresh temporary workspace and checks the answer and the files.

    venv/bin/python evals/run_evals.py                 # all cases
    venv/bin/python evals/run_evals.py --only dev-fix-bug,math-exact
    venv/bin/python evals/run_evals.py --persona tester --workers 2

Unit tests use fake models and can't catch a bad *answer* -- e.g. an agent
replying "Providing general guidance on X" instead of the guidance, or
stating a stale version from memory. This does. It spends real tokens.
Results are saved to evals/results/<timestamp>.json and compared with the
previous run.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
CASES_FILE = Path(__file__).resolve().parent / "cases.yaml"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
SAI = ROOT / "venv" / "bin" / "sai"
CASE_TIMEOUT = 300

# An "answer" that is only a status line about answering -- the bug where the
# Debugger replied "Providing general guidance on debugging..." and nothing else.
META_PREFIXES = ("providing", "requesting", "conducting", "i will", "i'll", "i am going to", "gathering",
                 "researching", "searching for", "looking into", "analyzing the")


@dataclass
class CaseResult:
    id: str
    persona: str
    passed: bool
    failures: List[str] = field(default_factory=list)
    route: Optional[str] = None
    agent: Optional[str] = None
    stop_reason: Optional[str] = None
    tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    answer: str = ""


def _fill(value, variables: Dict[str, str]):
    if isinstance(value, str):
        for k, v in variables.items():
            value = value.replace("{" + k + "}", v)
        return value
    if isinstance(value, list):
        return [_fill(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, variables) for k, v in value.items()}
    return value


def _draw_text_image(path: Path, text: str) -> None:
    """A screenshot-like PNG showing `text`, for vision cases."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (720, 160), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    draw.rectangle([8, 8, 712, 152], outline="red", width=4)
    for i, line in enumerate(text.splitlines()):
        draw.text((28, 30 + 50 * i), line, fill="black", font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def check(case: dict, data: dict, workspace: Path, before: Dict[str, str]) -> List[str]:
    """Returns failure messages (empty = pass). Pure function of the run's output -- unit tested."""
    expect = case.get("expect") or {}
    failures = []
    if data.get("is_error"):
        return [f"run errored: {data.get('error', '')[:200]}"]
    answer = str(data.get("result") or "")
    low = answer.lower()

    if not answer.strip():
        failures.append("empty answer")
    elif len(answer) < 200 and low.lstrip("*# ").startswith(META_PREFIXES) and not expect.get("allow_meta"):
        failures.append(f"meta answer instead of a real one: {answer[:100]!r}")

    if "route_in" in expect and data.get("route") not in expect["route_in"]:
        failures.append(f"route {data.get('route')!r} not in {expect['route_in']}")
    if "agent_in" in expect and data.get("agent") not in expect["agent_in"]:
        failures.append(f"agent {data.get('agent')!r} not in {expect['agent_in']}")
    for s in expect.get("contains_all", []):
        if s.lower() not in low:
            failures.append(f"answer lacks {s!r}")
    for key in sorted(k for k in expect if k.startswith("contains_any")):
        if not any(s.lower() in low for s in expect[key]):
            failures.append(f"answer has none of {expect[key]}")
    for s in expect.get("not_contains", []):
        if s.lower() in low:
            failures.append(f"answer contains forbidden {s!r}")
    if "regex" in expect and not re.search(expect["regex"], answer, re.IGNORECASE):
        failures.append(f"answer doesn't match /{expect['regex']}/")
    if "min_length" in expect and len(answer) < expect["min_length"]:
        failures.append(f"answer too short ({len(answer)} < {expect['min_length']} chars)")

    for rel in expect.get("file_exists", []):
        if not (workspace / rel).exists():
            failures.append(f"{rel} was not created")
    for rel in expect.get("file_missing", []):
        if (workspace / rel).exists():
            failures.append(f"{rel} should not exist")
    for rel, needles in (expect.get("file_contains") or {}).items():
        path = workspace / rel
        content = path.read_text(errors="ignore") if path.exists() else None
        for needle in ([needles] if isinstance(needles, str) else needles):
            if content is None or needle not in content:
                failures.append(f"{rel} doesn't contain {needle!r}")
    for rel in expect.get("file_unchanged", []):
        path = workspace / rel
        if not path.exists():
            failures.append(f"{rel} was deleted")
        elif path.read_text(errors="ignore") != before.get(rel):
            failures.append(f"{rel} was modified")
    return failures


def run_case(case: dict) -> CaseResult:
    case = _fill(case, {"current_year": str(date.today().year)})
    with tempfile.TemporaryDirectory(prefix=f"sai-eval-{case['id']}-") as tmp:
        workspace = Path(tmp)
        before = {}
        for rel, content in (case.get("files") or {}).items():
            (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
            (workspace / rel).write_text(content)
            before[rel] = content
        for rel, text in (case.get("images") or {}).items():
            _draw_text_image(workspace / rel, text)

        cmd = [str(SAI), "-p", case["prompt"], "--output-format", "json", "--quiet"]
        if case.get("accept_edits"):
            cmd.append("--accept-edits")
        started = time.time()
        try:
            proc = subprocess.run(cmd, cwd=workspace, input=case.get("stdin"), capture_output=True, text=True,
                                  timeout=CASE_TIMEOUT, stdin=None if case.get("stdin") else subprocess.DEVNULL)
            try:
                data = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
            except json.JSONDecodeError:
                data = {"is_error": True, "error": f"unparsable output: {proc.stdout[-300:]}"}
            if not data:
                data = {"is_error": True, "error": f"exit {proc.returncode}: {proc.stderr[-300:]}"}
        except subprocess.TimeoutExpired:
            data = {"is_error": True, "error": f"timed out after {CASE_TIMEOUT}s"}
        elapsed = time.time() - started

        failures = check(case, data, workspace, before)
        # When a file check fails, keep what the file actually ended up as -- the answer
        # alone can't tell "agent did the wrong thing" from "agent described it wrongly".
        expect = case.get("expect") or {}
        checked = set(expect.get("file_contains") or {}) | set(expect.get("file_unchanged") or [])
        for rel in sorted(checked):
            if any(f.startswith(rel) for f in failures) and (workspace / rel).is_file():
                failures.append(f"{rel} now reads: {(workspace / rel).read_text(errors='ignore')[:400]!r}")
        usage = data.get("usage") or {}
        return CaseResult(
            id=case["id"], persona=case.get("persona", ""), passed=not failures, failures=failures,
            route=data.get("route"), agent=data.get("agent"), stop_reason=data.get("stop_reason"),
            tokens=int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0)),
            cost_usd=float(usage.get("cost_usd") or 0.0),
            seconds=round(elapsed, 1), answer=str(data.get("result") or data.get("error") or "")[:2000],
        )


def _previous_results() -> Dict[str, bool]:
    runs = sorted(RESULTS_DIR.glob("*.json"))
    if not runs:
        return {}
    data = json.loads(runs[-1].read_text())
    return {r["id"]: r["passed"] for r in data.get("results", [])}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--persona", help="only cases for this persona")
    parser.add_argument("--workers", type=int, default=3, help="cases run in parallel (default 3)")
    args = parser.parse_args(argv)

    cases = yaml.safe_load(CASES_FILE.read_text())
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if args.persona:
        cases = [c for c in cases if c.get("persona") == args.persona]
    if not cases:
        print("No matching cases.")
        return 2

    previous = _previous_results()
    print(f"Running {len(cases)} case(s) with {args.workers} worker(s)...\n", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run_case, cases))

    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        change = ""
        if r.id in previous and previous[r.id] != r.passed:
            change = "  (was " + ("PASS" if previous[r.id] else "FAIL") + ")"
        who = r.agent or r.route or "-"
        print(f"{mark}  {r.id:<32} {who:<16} {r.tokens:>7,} tok  ${r.cost_usd:.4f} {r.seconds:>6.1f}s{change}")
        for f in r.failures:
            print(f"        - {f}")

    passed = sum(r.passed for r in results)
    tokens = sum(r.tokens for r in results)
    cost = sum(r.cost_usd for r in results)
    print(f"\n{passed}/{len(results)} passed · {tokens:,} tokens · ${cost:.3f}")

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"passed": passed, "total": len(results), "tokens": tokens, "cost_usd": round(cost, 4),
                               "results": [asdict(r) for r in results]}, indent=2))
    print(f"Saved {out.relative_to(ROOT)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
