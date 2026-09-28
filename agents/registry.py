from pathlib import Path
import yaml
from core.kernel import kernel
from agents.runtime import AgentRuntime

class AgentRegistry:
    def __init__(self):
        self.agents = []
        self.load()

    def load(self):
        # Resolved relative to this file, not the process's cwd -- `sai` is
        # meant to be launched from anywhere (see the `Hey sAI` shell
        # launcher), and a cwd-relative path silently found 0 configs (and
        # therefore 0 agents) whenever launched outside the project root.
        config_path = Path(__file__).resolve().parent / "configs"
        for file in config_path.glob("*.yaml"):
            try:
                with open(file, "r") as f:
                    data = yaml.safe_load(f)
                
                name = data["name"]
                try:
                    agent = kernel.get_agent(name)
                except KeyError:
                    agent = AgentRuntime(
                        name=name,
                        role=data["role"],
                        system_prompt=data["system_prompt"],
                        model=data["model"],
                        temperature=data.get("temperature", 0.2)
                    )
                    kernel.register_agent(name, agent)
                
                agent.priority = data.get("priority", 99)
                self.agents.append(agent)
            except Exception:
                pass
        self.agents.sort(key=lambda x: getattr(x, "priority", 99))

    def get_agents(self):
        return self.agents