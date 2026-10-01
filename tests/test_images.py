import base64

import pytest

from core.kernel import kernel
from llm.runtime import LLMRuntime, image_content
from ui.input import MAX_IMAGES, expand_file_mentions, is_image

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def test_image_mentions_are_attached_not_dumped(tmp_workspace):
    (tmp_workspace / "shot.png").write_bytes(PNG_1PX)
    text, attached = expand_file_mentions("what's wrong in @shot.png ?", tmp_workspace)
    assert attached == [(tmp_workspace / "shot.png").resolve()] and is_image(attached[0])
    assert "[Attached image: shot.png -- you can see it]" in text
    assert "PNG" not in text  # binary content is not pasted into the prompt


def test_image_limit(tmp_workspace):
    names = [f"s{i}.png" for i in range(MAX_IMAGES + 1)]
    for n in names:
        (tmp_workspace / n).write_bytes(PNG_1PX)
    text, attached = expand_file_mentions(" ".join("@" + n for n in names), tmp_workspace)
    assert sum(is_image(p) for p in attached) == MAX_IMAGES
    assert "not attached" in text


def test_image_content_parts(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(PNG_1PX)
    parts = image_content("describe", [str(img)])
    assert parts[0] == {"type": "text", "text": "describe"}
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,iVBOR")


def test_calls_with_images_use_the_vision_model(monkeypatch, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(PNG_1PX)
    seen = []

    class P:
        def stream(self, messages, model, temperature=0.2, **kw):
            seen.append((model, messages[0]["content"]))
            yield "{}"

    monkeypatch.setattr("core.settings.load_settings", lambda: {"vision_model": "vision/model"})
    monkeypatch.setattr("llm.router.ModelRouter.route", staticmethod(lambda kind: ("imgprov", "x")))
    kernel.register_provider("imgprov", P())
    try:
        LLMRuntime().query("look", task_kind="Coder", model="qwen/qwen3-coder-plus", images=[str(img)])
        LLMRuntime().query("no image", task_kind="Coder", model="qwen/qwen3-coder-plus")
    finally:
        kernel._providers.pop("imgprov", None)
    assert seen[0][0] == "vision/model" and isinstance(seen[0][1], list) and len(seen[0][1]) == 2
    assert seen[1] == ("qwen/qwen3-coder-plus", "no image")


def test_agent_forwards_images_from_the_users_request(fake_llm_runtime, tmp_workspace):
    from agents.runtime import AgentRuntime
    from core.orchestrator import Orchestrator
    from core.protocol import Message, MessageType
    from core.task import Task

    Orchestrator()  # registers the repository/tool services an agent turn needs
    kernel.register_service("llm_runtime", fake_llm_runtime)  # ...and replaces the LLM; put the fake back
    fake_llm_runtime.response = '{"summary": "s", "response": "r", "actions": []}'
    task = Task(goal="x")
    task.context.conversation.add(Message(sender="User", receiver="Coder", type=MessageType.TASK,
                                          payload={"content": "fix this", "images": ["/tmp/shot.png"]}))
    AgentRuntime("Coder", "codes", "You code.", "m").run(task.context)
    assert fake_llm_runtime.calls[-1]["images"] == ["/tmp/shot.png"]
