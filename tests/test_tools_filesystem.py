from tools.filesystem import ReadFileTool, WriteFileTool, ListDirectoryTool


def test_read_file_happy_path(tmp_workspace):
    (tmp_workspace / "hello.txt").write_text("hello world", encoding="utf-8")
    tool = ReadFileTool()
    result = tool.execute({"path": "hello.txt"})
    assert result == "hello world"


def test_read_file_missing_returns_error(tmp_workspace):
    tool = ReadFileTool()
    result = tool.execute({"path": "does_not_exist.txt"})
    assert result.startswith("Error:")


def test_read_file_outside_sandbox_rejected(tmp_workspace):
    tool = ReadFileTool()
    result = tool.execute({"path": "/definitely/outside/the/sandbox.txt"})
    assert "outside the workspace sandbox" in result


def test_write_file_creates_file_and_nested_dirs(tmp_workspace):
    tool = WriteFileTool()
    result = tool.execute({"path": "nested/dir/out.txt", "content": "written content"})
    assert result.startswith("Success:")
    written = (tmp_workspace / "nested" / "dir" / "out.txt").read_text(encoding="utf-8")
    assert written == "written content"


def test_write_file_outside_sandbox_rejected(tmp_workspace):
    tool = WriteFileTool()
    result = tool.execute({"path": "/definitely/outside/the/sandbox.txt", "content": "x"})
    assert "outside the workspace sandbox" in result


def test_list_directory_happy_path(tmp_workspace):
    (tmp_workspace / "a.txt").write_text("a", encoding="utf-8")
    (tmp_workspace / "sub").mkdir()
    tool = ListDirectoryTool()
    result = tool.execute({"path": ""})
    assert "a.txt" in result
    assert "sub" in result


def test_list_directory_outside_sandbox_rejected(tmp_workspace):
    tool = ListDirectoryTool()
    result = tool.execute({"path": "/definitely/outside/the/sandbox"})
    assert "outside the workspace sandbox" in result
