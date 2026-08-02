from dataclasses import dataclass, field
from typing import List, Dict, Set

@dataclass
class TaskNode:
    id: str
    description: str
    dependencies: List[str] = field(default_factory=list)
    state: str = "PENDING"  # PENDING, RUNNING, COMPLETED, FAILED

class DAGScheduler:
    def __init__(self):
        self.tasks: Dict[str, TaskNode] = {}

    def add_task(self, task_id: str, description: str, dependencies: List[str] = None) -> None:
        deps = dependencies if dependencies else []
        self.tasks[task_id] = TaskNode(id=task_id, description=description, dependencies=deps)

    def get_task(self, task_id: str) -> TaskNode:
        return self.tasks.get(task_id)

    def get_runnable_tasks(self) -> List[TaskNode]:
        runnable = []
        for task in self.tasks.values():
            if task.state == "PENDING":
                deps_ok = True
                for dep in task.dependencies:
                    dep_node = self.tasks.get(dep)
                    if not dep_node or dep_node.state != "COMPLETED":
                        deps_ok = False
                        break
                if deps_ok:
                    runnable.append(task)
        return runnable

    def mark_running(self, task_id: str) -> None:
        if task_id in self.tasks:
            self.tasks[task_id].state = "RUNNING"

    def mark_completed(self, task_id: str) -> None:
        if task_id in self.tasks:
            self.tasks[task_id].state = "COMPLETED"

    def mark_failed(self, task_id: str) -> None:
        if task_id in self.tasks:
            self.tasks[task_id].state = "FAILED"

    def is_completed(self) -> bool:
        if not self.tasks:
            return True
        return all(task.state == "COMPLETED" for task in self.tasks.values())

    def has_failed(self) -> bool:
        return any(task.state == "FAILED" for task in self.tasks.values())

    def has_cycles(self) -> bool:
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)
            node = self.tasks.get(node_id)
            if node:
                for dep in node.dependencies:
                    if dep not in visited:
                        if dfs(dep):
                            return True
                    elif dep in rec_stack:
                        return True
            rec_stack.remove(node_id)
            return False

        for task_id in self.tasks:
            if task_id not in visited:
                if dfs(task_id):
                    return True
        return False
