from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace

import httpx
from app.config import Settings
from app.llm import LLMClient
from app.main import create_app
from app.prompt import build_messages
from app.schemas import ChatMessage, ChatRequest
from app.text import LeadingThinkFilter
from fastapi.testclient import TestClient


def sse_bytes(
    contents: list[str],
    *,
    reasoning: list[str] | None = None,
    usage: dict | None = None,
) -> bytes:
    frames: list[str] = []
    for thought in reasoning or []:
        delta = {"reasoning_content": thought}
        frames.append(f"data: {json.dumps({'choices': [{'delta': delta}]})}\n\n")
    for content in contents:
        delta = {"content": content}
        frames.append(f"data: {json.dumps({'choices': [{'delta': delta}]})}\n\n")
    final: dict = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
    if usage:
        final["usage"] = usage
    frames.append(f"data: {json.dumps(final)}\n\n")
    frames.append("data: [DONE]\n\n")
    return "".join(frames).encode()


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        name = "message"
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            events.append((name, json.loads("\n".join(data))))
    return events


def base_settings(**overrides) -> Settings:
    settings = Settings(
        llm_api_key="test-key",
        fast_model="fast-model",
        deep_model="deep-model",
        cache_enabled=False,
        rate_limit_requests=1000,
        log_level="CRITICAL",
    )
    return replace(settings, **overrides)


@contextmanager
def client_for(handler, **overrides):
    settings = base_settings(**overrides)
    transport = httpx.MockTransport(handler)
    llm = LLMClient(settings, transport=transport)
    app = create_app(settings, llm_client=llm)
    with TestClient(app) as client:
        yield client


def ok_handler(contents: list[str] | None = None, *, reasoning: list[str] | None = None):
    body = sse_bytes(contents or ["Hello", " world"], reasoning=reasoning)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    return handler, calls


def test_health_index_and_assets():
    handler, _ = ok_handler()
    with client_for(handler) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        index = client.get("/")
        assert index.status_code == 200
        assert "__ASSET_VERSION__" not in index.text
        assert "Content-Security-Policy" in index.headers
        assert index.headers["X-Content-Type-Options"] == "nosniff"
        assert index.headers["X-Frame-Options"] == "DENY"

        css = client.get("/static/styles.css")
        assert css.status_code == 200
        assert css.headers["Cache-Control"].endswith("immutable")

        docs = client.get("/docs")
        assert docs.status_code == 200
        assert "cdn.jsdelivr.net" in docs.headers["Content-Security-Policy"]

        sw = client.get("/sw.js")
        assert sw.status_code == 200
        assert "__ASSET_VERSION__" not in sw.text
        assert sw.headers["Cache-Control"] == "no-cache"

        config = client.get("/api/config").json()
        assert config["ready"] is True
        assert config["max_message_chars"] == 4_000


def test_chat_streams_clean_content():
    # The think block is intentionally split across chunks.
    handler, calls = ok_handler(
        ["<thi", "nk>private reasoning</thi", "nk>Hello", " world"],
        reasoning=["internal thought"],
    )
    with client_for(handler) as client:
        response = client.post(
            "/api/chat",
            json={"message": "Hi", "history": [], "mode": "fast"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "done"
    text = "".join(data["text"] for name, data in events if name == "delta")
    assert text == "Hello world"
    assert "private reasoning" not in response.text
    assert "internal thought" not in response.text
    assert calls["count"] == 1
    done = events[-1][1]
    assert done["finish_reason"] == "stop"
    assert done["ttft_ms"] is not None


def test_chat_model_fallback_before_first_token():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["model"] == "fast-model":
            return httpx.Response(503, content=b'{"error": "upstream down"}')
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse_bytes(["Fallback answer"]),
        )

    with client_for(handler) as client:
        response = client.post(
            "/api/chat",
            json={"message": "Hi", "history": [], "mode": "fast"},
        )

    events = parse_sse(response.text)
    metas = [data for name, data in events if name == "meta"]
    assert any(meta["model"] == "deep-model" for meta in metas)
    assert "Fallback answer" in "".join(
        data["text"] for name, data in events if name == "delta"
    )


def test_fallback_after_stream_started_but_before_first_token():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["model"] == "fast-model":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b'data: {"error": {"message": "model crashed"}}\n\n',
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse_bytes(["Recovered"]),
        )

    with client_for(handler) as client:
        response = client.post(
            "/api/chat",
            json={"message": "Hi", "history": [], "mode": "fast"},
        )

    events = parse_sse(response.text)
    assert "Recovered" in "".join(
        data["text"] for name, data in events if name == "delta"
    )
    assert any(
        name == "meta" and data.get("fallback") is True
        for name, data in events
    )
    assert not [data for name, data in events if name == "error"]


def test_configured_fallback_precedes_mode_alternate():
    models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        models.append(json.loads(request.content)["model"])
        return httpx.Response(503, content=b'{"error": "down"}')

    with client_for(handler, fallback_model="backup-model") as client:
        client.post("/api/chat", json={"message": "Hi", "mode": "fast"})

    assert models == ["fast-model", "backup-model", "deep-model"]


def test_upstream_failure_is_user_safe():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"secret internal upstream details")

    with client_for(handler) as client:
        response = client.post(
            "/api/chat",
            json={"message": "Hi", "history": [], "mode": "fast"},
        )

    events = parse_sse(response.text)
    errors = [data for name, data in events if name == "error"]
    assert len(errors) == 1
    assert "secret internal" not in response.text
    assert "temporarily unavailable" in errors[0]["message"].lower()


def test_exact_prompt_cache():
    handler, calls = ok_handler(["Cached answer"])
    with client_for(handler, cache_enabled=True) as client:
        first = client.post(
            "/api/chat",
            json={"message": "What is uptime?", "history": [], "mode": "fast"},
        )
        second = client.post(
            "/api/chat",
            json={"message": "What is uptime?", "history": [], "mode": "fast"},
        )

    assert first.headers.get("X-Cache") != "HIT"
    assert second.headers.get("X-Cache") == "HIT"
    assert calls["count"] == 1
    second_events = parse_sse(second.text)
    assert second_events[0][1]["cached"] is True
    assert second_events[-1][1]["cached"] is True


def test_rate_limit():
    handler, _ = ok_handler()
    with client_for(handler, rate_limit_requests=2) as client:
        assert client.post("/api/chat", json={"message": "one"}).status_code == 200
        assert client.post("/api/chat", json={"message": "two"}).status_code == 200
        blocked = client.post("/api/chat", json={"message": "three"})

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["Retry-After"]) >= 1


def test_validation_and_size_limits():
    handler, _ = ok_handler()
    with client_for(handler, max_message_chars=200) as client:
        blank = client.post("/api/chat", json={"message": "   "})
        assert blank.status_code == 422
        assert blank.json()["error"]["code"] == "invalid_request"

        long_message = client.post("/api/chat", json={"message": "x" * 201})
        assert long_message.status_code == 413

        bad_mode = client.post("/api/chat", json={"message": "hi", "mode": "turbo"})
        assert bad_mode.status_code == 422

        huge_body = client.post(
            "/api/chat",
            content=b'{"message":"' + b"x" * 70_000 + b'"}',
            headers={"Content-Type": "application/json"},
        )
        assert huge_body.status_code == 413
        assert huge_body.json()["error"]["code"] == "payload_too_large"
        assert huge_body.json()["error"]["request_id"]


def test_unconfigured_instance_is_not_ready():
    handler, _ = ok_handler()
    remote = "https://router.huggingface.co/v1"
    with client_for(handler, llm_api_key="", llm_base_url=remote) as client:
        assert client.get("/readyz").status_code == 503
        assert client.get("/readyz").json()["reason"] == "missing_api_key"
        assert client.get("/api/config").json()["ready"] is False
        response = client.post("/api/chat", json={"message": "hi"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "service_unavailable"


def test_local_backend_is_ready_without_api_key():
    settings = Settings(
        llm_api_key="",
        llm_base_url="http://localhost:11434/v1",
        fast_model="qwen2.5-coder:7b",
    )
    assert settings.is_local_backend is True
    assert settings.is_ready is True


def test_ollama_env_configuration(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:11434/v1/")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
    monkeypatch.setenv("HF_API_KEY", "")
    for name in ("FAST_MODEL", "DEEP_MODEL", "MODEL"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.llm_base_url == "http://127.0.0.1:11434/v1"
    assert settings.fast_model == "qwen2.5-coder:7b"
    assert settings.deep_model == "qwen2.5-coder:7b"
    assert settings.provider == ""
    assert settings.is_ready is True


def test_stats_endpoint_requires_opt_in():
    handler, _ = ok_handler()
    with client_for(handler) as client:
        assert client.get("/api/stats").status_code == 404
    with client_for(handler, stats_enabled=True) as client:
        assert client.get("/api/stats").status_code == 200


def test_think_filter_handles_boundaries():
    filter_ = LeadingThinkFilter()
    chunks = ["  <thi", "nk>hidden</thi", "nk>Visible", " text"]
    output = "".join(filter_.feed(chunk) for chunk in chunks)
    assert output == "Visible text"
    assert filter_.reasoning_started is True
    assert filter_.reasoning_finished is True

    passthrough = LeadingThinkFilter()
    output = "".join(passthrough.feed(chunk) for chunk in ["Use `", "<think>", "` in code"])
    assert output == "Use `<think>` in code"


def test_provider_can_be_disabled(monkeypatch):
    monkeypatch.setenv("PROVIDER", "")
    assert Settings.from_env().provider == ""


def test_history_trimming_and_role_merging():
    settings = base_settings(max_history_messages=4, max_history_chars=20)
    history = [
        ChatMessage(role="assistant", content="orphan"),
        ChatMessage(role="user", content="first"),
        ChatMessage(role="user", content="second"),
        ChatMessage(role="assistant", content="third"),
        ChatMessage(role="user", content="fourth"),
    ]
    messages = build_messages(
        ChatRequest(message="latest", history=history, mode="fast"), settings
    )
    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"].endswith("latest")
    assert messages[1]["role"] == "user"
    assert all(
        left["role"] != right["role"]
        for left, right in zip(messages[1:], messages[2:], strict=False)
    )
