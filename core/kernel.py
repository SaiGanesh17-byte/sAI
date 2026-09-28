from typing import Any, Dict

class Kernel:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(Kernel, cls).__new__(cls, *args, **kwargs)
            cls._instance._services = {}
            cls._instance._tools = {}
            cls._instance._providers = {}
            cls._instance._agents = {}
        return cls._instance

    def register_service(self, name: str, service: Any) -> None:
        self._services[name] = service

    def register_tool(self, name: str, tool: Any) -> None:
        self._tools[name] = tool

    def register_provider(self, name: str, provider: Any) -> None:
        self._providers[name] = provider

    def register_agent(self, name: str, agent: Any) -> None:
        self._agents[name] = agent

    def get_service(self, name: str) -> Any:
        service = self._services.get(name)
        if not service:
            raise KeyError(f"Service '{name}' is not registered with the Kernel.")
        return service

    def get_tool(self, name: str) -> Any:
        tool = self._tools.get(name)
        if not tool:
            raise KeyError(f"Tool '{name}' is not registered with the Kernel.")
        return tool

    def get_provider(self, name: str) -> Any:
        provider = self._providers.get(name)
        if not provider:
            raise KeyError(f"Provider '{name}' is not registered with the Kernel.")
        return provider

    def get_agent(self, name: str) -> Any:
        agent = self._agents.get(name)
        if not agent:
            raise KeyError(f"Agent '{name}' is not registered with the Kernel.")
        return agent

    def list_agents(self) -> Dict[str, Any]:
        return dict(self._agents)

# Singleton instance
kernel = Kernel()
