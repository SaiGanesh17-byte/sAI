"""
Jev -- TypeSafe AI's "System One" fast structured-decision engine.

Jev is not a conversational agent. It makes one constrained LLM call per
REPL turn to classify the user's input into a routing decision, returned as
strict JSON, rather than producing free-form conversational text. It is the
fast/intuitive front door; the existing Planner -> Architect -> Researcher ->
Coder -> Reviewer loop remains the slow/deliberate System Two.

Jev must never block the user: any parse failure, LLM error, or invalid
decision degrades to a safe "full_orchestrator" fallback rather than raising.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.kernel import kernel
from core.settings import load_settings

VALID_ROUTES = {"direct_answer", "single_agent", "full_orchestrator"}

SYSTEM_PROMPT = """You are Jev, TypeSafe AI's fast "System One" decision engine for the sAI platform.
You do not chat or write long conversational replies. You make a single fast, structured
routing decision about how the user's message should be handled.

Respond ONLY with a JSON object with these keys:
- "route": one of "direct_answer", "single_agent", "full_orchestrator".
- "agent": if route is "single_agent", the exact name of ONE agent from the available agents list. Otherwise null.
- "answer": if route is "direct_answer", your direct reply text to the user. Otherwise null.
- "reasoning": one short sentence explaining the decision.
- "confidence": float between 0.0 and 1.0.

Routing rules:
- "direct_answer": trivial or conversational input only -- greetings, thanks, small talk,
  or meta questions about sAI itself that need no repository access or tools. NEVER use
  this route for a question whose correct answer could have changed since your training
  data was cut off -- "latest"/"newest"/"current"/"most recent" anything, current
  versions of external software/models/products, current prices, current events, or any
  other fact you are not certain is still true today. Answering those from memory risks
  confidently stating stale information as if it were current -- that failure mode is
  worse than a slower answer. Route those to "single_agent" with the Researcher agent
  instead, if one is available in the list below.
  Also NEVER do arithmetic or any calculation in a direct answer (you get multi-digit
  products wrong) -- route it to "single_agent" with the Researcher, which has an exact
  math tool. Today's date/year is given below, so "what year is it" is fine to answer.
- "single_agent": a narrow request that clearly maps to exactly ONE agent's stated role
  in the available agents list below -- match on what each agent's role description
  actually says, not on generic assumptions. This includes every time-sensitive factual
  question described above -- route it to whichever agent's role covers live/external
  research (e.g. Researcher), not to "direct_answer" from memory. If a request touches
  more than one agent's distinct area of responsibility (e.g. both security AND
  licensing), that is NOT a single-agent fit.
  A QUESTION -- how-to, explanation, advice, plan, estimate, comparison, writing that
  the user only wants to read, or any request that says not to change anything ("don't
  edit", "just tell me what would change") -- is "single_agent" with the one agent whose role fits
  best (e.g. a migration plan -> Architect, an estimate or user stories -> Writer, a
  debugging how-to -> Debugger), even if the topic is broad. Answering a question never
  needs the whole team.
  Each agent's [brackets] say what it can actually do. A request to run, execute, install,
  build or test something needs an agent that "runs commands"; one to change files needs
  one that "edits files" -- never route those to an agent that can NOT do it.
- "full_orchestrator": only for real multi-step WORK on this workspace that needs
  several agents in sequence (e.g. design + implement + test a new feature). When you
  are unsure between "single_agent" and this route for a plain question, choose
  "single_agent".

Do not include markdown fences or any text outside the JSON object.
"""


# "what is 17 * 23 * 41?", "calculate (2^10)/4", "17*23" -- plain arithmetic only.
_ARITH_ONLY = re.compile(
    r"^\s*(?:(?:what\s+is|what's|whats|calculate|compute|evaluate|solve)\s+)?"
    r"(?P<expr>[\d\s.,+\-*/x×÷^()%]+?)"
    r"\s*(?:\?|=\s*\??)?\s*(?:[.!]?\s*(?:just the (?:number|answer|result)|only the (?:number|answer))\.?)?\s*$",
    re.IGNORECASE,
)


def quick_math(text: str) -> Optional[str]:
    """
    Exact answer for a message that is just arithmetic, via sympy -- no LLM. Models
    (Jev and agents alike) state wrong products with confidence (17*23*41 came back
    as 15331 and 8,351). Anything wordier returns None and is routed normally.
    """
    match = _ARITH_ONLY.match(text or "")
    if not match:
        return None
    expr = match.group("expr").strip()
    if re.search(r"\b\d{4}-\d{1,2}-\d{1,2}\b", expr):
        return None  # a date like 2026-10-01, not a subtraction
    if not re.search(r"\d", expr) or not re.search(r"\d\s*[+\-*/x×÷^%]\s*\(?\s*\d", expr):
        return None  # needs at least one operator between numbers
    normalized = (expr.replace(",", "").replace("×", "*").replace("x", "*").replace("X", "*")
                  .replace("÷", "/").replace("^", "**"))
    try:
        from sympy import Integer, Rational, nsimplify, sympify
        value = sympify(normalized, rational=True)
        if not value.is_number:
            return None
        if value.is_Integer or (isinstance(value, Rational) and value.q == 1):
            shown = str(int(value))
        elif isinstance(value, Rational):
            shown = f"{value} ≈ {float(value):.10g}"
        else:
            shown = f"{float(value):.10g}"
    except Exception:
        return None
    return f"{expr} = **{shown}**"


@dataclass
class JevDecision:
    route: str
    agent: Optional[str] = None
    answer: Optional[str] = None
    reasoning: str = ""
    confidence: float = 0.0
    fallback: bool = False


# Shown next to each agent's role. Jev used to route on role text alone, and "run this
# command and tell me the output" went to the Researcher ("finds out what is true"),
# which has no execute_command -- it answered with a web search about the command.
_CAPABILITIES = [("execute_command", "runs commands"), ("edit_file", "edits files"),
                 ("web_search", "searches the web")]


def agent_capabilities(name: str) -> str:
    try:
        from core.kernel import kernel
        agent = kernel.list_agents().get(name)
    except Exception:
        agent = None
    if agent is None or not hasattr(agent, "allows_tool"):
        return ""
    can = ["reads code"] + [label for tool, label in _CAPABILITIES if agent.allows_tool(tool)]
    servers = sorted({p.split("__")[1] for p in getattr(agent, "tools", []) if p.startswith("mcp__") and "__" in p[5:]})
    can += [f"uses {server} tools" for server in servers]
    cannot = [label.replace("runs", "run").replace("edits", "edit").replace("searches", "search")
              for tool, label in _CAPABILITIES if not agent.allows_tool(tool)]
    return f" [{', '.join(can)}" + (f"; can NOT {', '.join(cannot)}" if cannot else "") + "]"


_FILE_TOKEN = re.compile(r"[\w./-]+\.[A-Za-z0-9]{1,6}\b")


def mentioned_workspace_file(text: str) -> Optional[str]:
    """The first token in `text` naming an existing file or folder in the workspace."""
    try:
        from core.security import get_current_workspace
        workspace = get_current_workspace().resolve()
    except Exception:
        return None
    for token in _FILE_TOKEN.findall(text or ""):
        token = token.strip("./") if token.startswith("./") else token.rstrip(".")
        if not token or token.startswith(("http", "/")) or ".." in token:
            continue
        try:
            if (workspace / token).exists():
                return token
        except OSError:
            continue
    return None


class JevRouter:
    """
    Fast structured-decision router. Wraps a single constrained LLM call
    behind a defensive parse/validate pipeline that always resolves to a
    usable JevDecision.
    """

    def decide(
        self,
        goal: str,
        available_agents: List[str],
        recent_turns: Optional[List[str]] = None,
        agent_roles: Optional[Dict[str, str]] = None,
    ) -> JevDecision:
        goal = (goal or "").strip()
        if not goal:
            return self._fallback("Empty input.")

        quick = quick_math(goal)
        if quick is not None:
            return JevDecision(route="direct_answer", answer=quick, reasoning="Plain arithmetic, computed exactly.",
                               confidence=1.0)

        prompt = self._build_prompt(goal, available_agents, recent_turns, agent_roles)

        settings = load_settings()
        kwargs = {}
        if settings.get("jev_json_mode"):
            kwargs["response_format"] = {"type": "json_object"}

        try:
            llm_runtime = kernel.get_service("llm_runtime")
            raw_response = llm_runtime.query(prompt, task_kind="Jev", temperature=0.0, **kwargs)
        except Exception as e:
            return self._fallback(f"Jev LLM call failed: {e}")

        parsed = self._parse(raw_response)
        if parsed is None:
            return self._fallback("Jev could not parse a structured decision from the LLM output.")

        route = str(parsed.get("route", "")).strip()
        if route not in VALID_ROUTES:
            return self._fallback(f"Jev returned an unrecognized route: '{route}'.")

        reasoning = str(parsed.get("reasoning", "") or "")
        try:
            confidence = float(parsed.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        if route == "single_agent":
            agent = str(parsed.get("agent", "") or "").strip()
            matched = next((a for a in available_agents if a.lower() == agent.lower()), None)
            if not matched:
                return self._fallback(f"Jev picked an unknown agent: '{agent}'.")
            return JevDecision(route="single_agent", agent=matched, reasoning=reasoning, confidence=confidence)

        if route == "direct_answer":
            answer = parsed.get("answer")
            if not answer or not str(answer).strip():
                return self._fallback("Jev chose direct_answer but returned no answer text.")
            mentioned = mentioned_workspace_file(goal)
            reader = next((a for a in ("Researcher", "Debugger", "Coder") if a in available_agents), None)
            if mentioned and reader:
                # Jev can't see files; answering "what does error.png say?" itself gave
                # "I don't have access to that image" while an agent could just read it.
                return JevDecision(route="single_agent", agent=reader, confidence=confidence,
                                   reasoning=f"Mentions {mentioned}, which only an agent can read.")
            return JevDecision(route="direct_answer", answer=str(answer), reasoning=reasoning, confidence=confidence)

        return JevDecision(route="full_orchestrator", reasoning=reasoning, confidence=confidence)

    def _build_prompt(
        self,
        goal: str,
        available_agents: List[str],
        recent_turns: Optional[List[str]],
        agent_roles: Optional[Dict[str, str]] = None,
    ) -> str:
        if agent_roles:
            agents_list = "\n".join(f"- {name}: {agent_roles.get(name, '')}{agent_capabilities(name)}"
                                    for name in available_agents)
        else:
            agents_list = ", ".join(available_agents) if available_agents else "(none registered)"
        history = ""
        if recent_turns:
            history = "\nRecent conversation (most recent last):\n" + "\n".join(f"- {t}" for t in recent_turns[-4:])

        from agents.runtime import current_date_note
        return f"""{SYSTEM_PROMPT}

{current_date_note()}

Available agents: {agents_list}
{history}

User message: {goal}
"""

    def _parse(self, content: str) -> Optional[dict]:
        cleaned = (content or "").strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{.*\}", content or "", re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    def _fallback(self, reasoning: str) -> JevDecision:
        return JevDecision(route="full_orchestrator", reasoning=reasoning, confidence=0.0, fallback=True)


# Friendly alias matching the "Jev" brand name.
Jev = JevRouter
