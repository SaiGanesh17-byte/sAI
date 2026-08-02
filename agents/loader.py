from pathlib import Path
import yaml
from core.kernel import kernel
from agents.runtime import AgentRuntime

class AgentLoader:
    @staticmethod
    def load_agents(configs_path: str | Path) -> None:
        """
        Loads all agent configs from YAML files and registers them in the Kernel.
        """
        c_path = Path(configs_path)
        for f in c_path.glob("*.yaml"):
            try:
                with open(f, "r") as file:
                    data = yaml.safe_load(file)
                name = data["name"]
                role = data["role"]
                system_prompt = data["system_prompt"]
                model = data["model"]
                temperature = data.get("temperature", 0.2)
                
                agent = AgentRuntime(
                    name=name,
                    role=role,
                    system_prompt=system_prompt,
                    model=model,
                    temperature=temperature
                )
                kernel.register_agent(name, agent)
            except Exception:
                pass
