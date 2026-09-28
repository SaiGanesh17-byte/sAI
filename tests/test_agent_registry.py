from pathlib import Path

import pytest
import yaml

from agents.registry import AgentRegistry

CONFIG_DIR = Path("agents/configs")
REQUIRED_KEYS = {"name", "role", "model", "system_prompt"}

EXPECTED_AGENT_NAMES = {
    "ProjectManager", "Planner", "BusinessAnalyst", "Architect", "Database",
    "DataEngineer", "UIDesigner", "DataScientist", "Researcher", "WebSearch",
    "Coder", "APIIntegration", "Mathematics", "Performance", "Observability",
    "Security", "Testing", "QAAnalyst", "Debugger", "Reviewer", "GitOps",
    "Documentation", "DevOps", "LegalFinance",
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
    assert EXPECTED_AGENT_NAMES <= loaded_names, (
        f"Missing expected agents: {EXPECTED_AGENT_NAMES - loaded_names}"
    )


def test_registry_agents_sorted_by_priority():
    agents = AgentRegistry().get_agents()
    priorities = [getattr(a, "priority", 99) for a in agents]
    assert priorities == sorted(priorities)
