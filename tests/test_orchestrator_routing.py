from core.orchestrator import Orchestrator
from core.protocol import Message, MessageType, AgentResponse
from core.task import Task


class FakeAgent:
    """
    Minimal stand-in for an agents/runtime.py::AgentRuntime instance -- only
    exposes what Orchestrator.run() actually touches (.name and .run()), so
    these tests exercise the orchestrator's own control flow (next_agent
    validation, start_agent resume) without going through the real
    PromptBuilder/LLM pipeline.
    """

    def __init__(self, name, next_agent=None, finished=False):
        self.name = name
        self.priority = 0
        self.next_agent = next_agent
        self.finished = finished
        self.call_count = 0

    def run(self, context):
        self.call_count += 1
        response = AgentResponse(
            agent=self.name,
            summary=f"{self.name} did work",
            reasoning=[],
            actions=[],
            confidence=0.9,
            next_agent=self.next_agent,
            finished=self.finished,
        )
        msg = Message(
            sender=self.name, receiver="Orchestrator", type=MessageType.SUMMARY, payload={"summary": response.summary}
        )
        msg.metadata["response"] = response
        return msg


def _orchestrator_with_agents(agents):
    orchestrator = Orchestrator()
    orchestrator.agents = agents
    return orchestrator


def test_invalid_next_agent_falls_back_to_priority_order():
    # Orchestrator.run() always starts from an agent named "Planner".
    agent_a = FakeAgent("Planner", next_agent="TotallyMadeUpAgent", finished=False)
    agent_b = FakeAgent("B", finished=True)
    orchestrator = _orchestrator_with_agents([agent_a, agent_b])

    task = Task(goal="do the thing")
    orchestrator.run(task)

    assert agent_a.call_count == 1
    # Before the fix, an unrecognized next_agent name would silently end the
    # loop here (agent lookup returns None) -- B would never run.
    assert agent_b.call_count == 1


def test_empty_next_agent_falls_back_to_priority_order():
    agent_a = FakeAgent("Planner", next_agent=None, finished=False)
    agent_b = FakeAgent("B", finished=True)
    orchestrator = _orchestrator_with_agents([agent_a, agent_b])

    task = Task(goal="do the thing")
    orchestrator.run(task)

    assert agent_a.call_count == 1
    assert agent_b.call_count == 1


def test_valid_next_agent_is_honored():
    agent_a = FakeAgent("Planner", next_agent="B", finished=False)
    agent_b = FakeAgent("B", finished=True)
    orchestrator = _orchestrator_with_agents([agent_a, agent_b])

    task = Task(goal="do the thing")
    orchestrator.run(task)

    assert agent_a.call_count == 1
    assert agent_b.call_count == 1


def test_start_agent_resumes_without_rerunning_earlier_agents():
    agent_a = FakeAgent("A", finished=False)
    agent_b = FakeAgent("B", finished=True)
    orchestrator = _orchestrator_with_agents([agent_a, agent_b])

    # goal deliberately looks like a greeting -- with start_agent set, the
    # greeting short-circuit and initial planning message must be skipped.
    task = Task(goal="hi")
    orchestrator.run(task, start_agent="B")

    assert agent_a.call_count == 0
    assert agent_b.call_count == 1

    messages = task.context.conversation.all()
    assert not any(getattr(m, "receiver", None) == "Planner" for m in messages)


def test_default_run_still_starts_from_planner_named_agent():
    # Confirms start_agent=None (the default) preserves the original behavior:
    # a normal run still starts from the first configured agent.
    agent_planner = FakeAgent("Planner", finished=True)
    orchestrator = _orchestrator_with_agents([agent_planner])

    task = Task(goal="build something")
    orchestrator.run(task)

    assert agent_planner.call_count == 1
