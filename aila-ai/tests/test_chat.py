import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.llm import MockModel, OpenAICompatibleModel
from app.main import ChatMessage, app, build_messages, get_model


class FakeModel:
    name = "fake"

    def __init__(self, chunks: list[str], fail: Exception | None = None) -> None:
        self.chunks = chunks
        self.fail = fail
        self.received: list[dict[str, str]] = []

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        self.received = messages
        for c in self.chunks:
            yield c
        if self.fail:
            raise self.fail


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app)
    app.dependency_overrides.clear()


def use_model(model: object) -> None:
    app.dependency_overrides[get_model] = lambda: model


def parse_sse(body: str) -> list[dict[str, str]]:
    return [json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")]


def test_stream_emits_text_emotion_action_and_done(client: TestClient) -> None:
    use_model(FakeModel(["[hap", "py]你好。[nod]", "很高兴见到你。"]))
    res = client.post("/chat", json={"messages": [{"role": "user", "content": "你好"}]})

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert parse_sse(res.text) == [
        {"type": "emotion", "name": "happy"},
        {"type": "text", "text": "你好。"},
        {"type": "action", "name": "nod"},
        {"type": "text", "text": "很高兴见到你。"},
        {"type": "done"},
    ]


def test_system_prompt_is_sent_first(client: TestClient) -> None:
    model = FakeModel(["好的"])
    use_model(model)
    client.post("/chat", json={"messages": [{"role": "user", "content": "在吗"}]})

    assert model.received[0]["role"] == "system"
    assert "艾拉" in model.received[0]["content"]
    assert model.received[-1] == {"role": "user", "content": "在吗"}


def test_model_failure_becomes_error_event(client: TestClient) -> None:
    use_model(FakeModel(["说到一半"], fail=RuntimeError("boom")))
    events = parse_sse(client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}).text)

    assert events[0] == {"type": "text", "text": "说到一半"}
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "unknown"


def test_last_message_must_be_from_user(client: TestClient) -> None:
    use_model(FakeModel([]))
    res = client.post("/chat", json={"messages": [{"role": "assistant", "content": "你好"}]})
    assert res.status_code == 422


def test_empty_history_is_rejected(client: TestClient) -> None:
    use_model(FakeModel([]))
    assert client.post("/chat", json={"messages": []}).status_code == 422


def test_build_messages_trims_and_merges() -> None:
    history = [
        ChatMessage(role="user", content="1"),
        ChatMessage(role="assistant", content="2"),
        ChatMessage(role="user", content="3"),
        ChatMessage(role="user", content="4"),
    ]
    # 只留最近 3 条时第一条是 assistant，要丢掉；连续两条用户消息合并
    assert build_messages(history, max_history=3)[1:] == [{"role": "user", "content": "3\n4"}]


def test_health(client: TestClient) -> None:
    use_model(FakeModel([]))
    assert client.get("/health").json() == {"ok": True, "model": "fake"}


def test_unreachable_model_is_reported_as_network_error(client: TestClient) -> None:
    # 真的发 HTTP 请求到一个没人监听的端口，验证异常归类
    settings = replace(load_settings(), llm_api_key="test-key", llm_base_url="http://127.0.0.1:9")
    use_model(OpenAICompatibleModel(settings))
    events = parse_sse(client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}).text)
    assert events == [{"type": "error", "code": "network", "message": "连不上大模型服务"}]


def test_mock_model_goes_through_the_whole_pipeline(client: TestClient) -> None:
    use_model(MockModel(delay=0))
    events = parse_sse(client.post("/chat", json={"messages": [{"role": "user", "content": "你好"}]}).text)
    types = [e["type"] for e in events]

    assert types[0] == "emotion"
    assert "action" in types
    assert types[-1] == "done"
    assert "[" not in "".join(e.get("text", "") for e in events)
