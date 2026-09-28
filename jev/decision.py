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
  worse than a slower answer. Route those to "single_agent" with the WebSearch agent
  instead, if one is available in the list below.
- "single_agent": a narrow request that clearly maps to exactly ONE agent's stated role
  in the available agents list below -- match on what each agent's role description
  actually says, not on generic assumptions. This includes every time-sensitive factual
  question described above -- route it to whichever agent's role covers live/external
  research (e.g. WebSearch), not to "direct_answer" from memory. If a request touches
  more than one agent's distinct area of responsibility (e.g. both security AND
  licensing), that is NOT a single-agent fit.
- "full_orchestrator": anything spanning multiple concerns, multi-step work, or anything
  you are not highly confident fits exactly one agent's role. When in doubt, choose this
  route.

Do not include markdown fences or any text outside the JSON object.
"""


@dataclass
class JevDecision:
    route: str
    agent: Optional[str] = None
    answer: Optional[str] = None
    reasoning: str = ""
    confidence: float = 0.0
    fallback: bool = False


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
            agents_list = "\n".join(f"- {name}: {agent_roles.get(name, '')}" for name in available_agents)
        else:
            agents_list = ", ".join(available_agents) if available_agents else "(none registered)"
        history = ""
        if recent_turns:
            history = "\nRecent conversation (most recent last):\n" + "\n".join(f"- {t}" for t in recent_turns[-4:])

        return f"""{SYSTEM_PROMPT}

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
