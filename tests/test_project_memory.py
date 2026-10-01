import subprocess

from memory.graphiti import GraphitiMemory


def test_memory_is_separate_per_workspace(tmp_path):
    a, b = tmp_path / "app-a", tmp_path / "app-b"
    GraphitiMemory(a).add_fact("A uses React Native")
    assert GraphitiMemory(a).facts == ["A uses React Native"]
    assert GraphitiMemory(b).facts == []  # no leaking into other projects


def test_memory_tool_uses_the_current_workspace(tmp_workspace):
    from tools.memory import MemoryTool
    MemoryTool().execute({"action": "add_fact", "text": "uses pytest"})
    assert GraphitiMemory(tmp_workspace).facts == ["uses pytest"]


def test_repository_note_reads_the_github_remote(tmp_workspace):
    from agents.runtime import repository_note
    assert "not a git repository" in repository_note()
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_workspace)], check=True)
    subprocess.run(["git", "-C", str(tmp_workspace), "remote", "add", "origin", "git@github.com:octo/hello-world.git"], check=True)
    note = repository_note()
    assert "GitHub repository octo/hello-world" in note and "https://github.com/octo/hello-world" in note
    assert "current branch: main" in note
