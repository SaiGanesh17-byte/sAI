from pathlib import Path

import pytest
import yaml

from agents.registry import AgentRegistry

CONFIG_DIR = Path("agents/configs")
REQUIRED_KEYS = {"name", "role", "model", "system_prompt"}

EXPECTED_AGENT_NAMES = {
    "Planner", "Architect", "Coder", "Debugger", "Reviewer", "Researcher", "DevOps", "Writer",
}


@pytest.mark.parametrize("config_path", sorted(CONFIG_DIR.glob("*.yaml")), ids=lambda p: p.name)
def test_each_config_is_valid_yaml_with_required_keys(config_path):
    with open(config_path) as f:
        data = yaml.safe_load(f)

    missing = REQUIRED_KEYS - data.keys()
    assert not missing, f"{config_path.name} is missing required keys: {missing}"
    assert data["system_prompt"].strip(), f"{config_path.name} has an empty system_prompt"
    assert len(data["system_prompt"].strip()) > 40, (
        f"{config_path.name} system_prompt looks like a stub, not a real spec"
    )


def test_agent_names_are_unique_across_configs():
    names = []
    for config_path in CONFIG_DIR.glob("*.yaml"):
        with open(config_path) as f:
            names.append(yaml.safe_load(f)["name"])
    assert len(names) == len(set(names)), f"Duplicate agent names found: {names}"


def test_registry_loads_full_expected_roster():
    agents = AgentRegistry().get_agents()
    loaded_names = {a.name for a in agents}
    assert EXPECTED_AGENT_NAMES == loaded_names, (
        f"Missing expected agents: {EXPECTED_AGENT_NAMES - loaded_names}"
    )


def test_registry_agents_sorted_by_priority():
    agents = AgentRegistry().get_agents()
    priorities = [getattr(a, "priority", 99) for a in agents]
    assert priorities == sorted(priorities)


KNOWN_TOOLS = {
    "read_file", "write_file", "patch_file", "edit_file", "list_directory", "execute_command",
    "bash_output", "kill_shell", "git_operation", "web_search", "web_fetch", "grep_ast",
    "codebase_search", "glob", "grep", "run_python_script", "memory_operation", "math_solve", "todo_write", "delegate",
}


@pytest.mark.parametrize("config_path", sorted(CONFIG_DIR.glob("*.yaml")), ids=lambda p: p.name)
def test_tool_lists_name_real_tools(config_path):
    tools = yaml.safe_load(open(config_path)).get("tools", ["*"])
    unknown = [t for t in tools if "*" not in t and t not in KNOWN_TOOLS]
    assert not unknown, f"{config_path.name} lists tools that don't exist: {unknown}"


def test_read_only_agents_cannot_write_or_run_commands():
    agents = {a.name: a for a in AgentRegistry().get_agents()}
    for name in ("Reviewer", "Researcher", "Planner"):
        for tool in ("write_file", "edit_file", "patch_file", "execute_command", "git_operation"):
            assert not agents[name].allows_tool(tool), f"{name} should not have {tool}"
    assert agents["Coder"].allows_tool("execute_command") and agents["Coder"].allows_tool("mcp__github__x")
    assert agents["Researcher"].allows_tool("web_fetch") and agents["Researcher"].allows_tool("mcp__docs__search")
    assert not agents["Writer"].allows_tool("execute_command") and agents["Writer"].allows_tool("edit_file")


def test_orchestrator_enforces_tool_lists():
    from core.orchestrator import Orchestrator
    from core.task import Task

    orch = Orchestrator()
    reviewer = next(a for a in orch.agents if a.name == "Reviewer")
    outcome = orch.execute_action(Task(goal=""), reviewer, {"tool": "write_file", "args": {"path": "x.py", "content": ""}})
    assert outcome.status == "failed" and "can't use 'write_file'" in outcome.content


def test_agent_prompt_lists_only_allowed_tools(fake_llm_runtime, tmp_workspace):
    from core.orchestrator import Orchestrator
    from core.kernel import kernel
    from core.task import Task

    orch = Orchestrator()
    kernel.register_service("llm_runtime", fake_llm_runtime)  # Orchestrator() re-registers the real one
    fake_llm_runtime.response = '{"summary": "ok", "response": "ok", "actions": [], "findings": []}'
    reviewer = next(a for a in orch.agents if a.name == "Reviewer")
    reviewer.run(Task(goal="review").context)
    prompt = fake_llm_runtime.calls[-1]["prompt"]
    tools_section = prompt.split("AVAILABLE TOOLS:")[1].split("ENVIRONMENT:")[0]
    assert "read_file" in tools_section and "write_file" not in tools_section and "execute_command" not in tools_section
