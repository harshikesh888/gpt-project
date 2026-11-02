# Jarvis AI

A production-ready, streaming AI assistant built with FastAPI and a framework-free vanilla JavaScript client with no build step. It is designed to be fast on first load, safe to expose publicly, and simple to deploy.

![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-blue)

## Highlights

- **True streaming** — async Server-Sent Events from a local Ollama model (or any OpenAI-compatible backend) to the browser, with keep-alive heartbeats and backpressure.
- **Fast and Deep modes** — route everyday questions to a low-latency model and complex questions to a reasoning model.
- **Automatic model fallback** — if a model is unavailable before the first token, the next configured model is tried.
- **Fast by default** — pooled upstream connections, bounded token budgets, exact-match response caching, local versioned assets, compressed responses, lazy syntax highlighting, and throttled streaming renders.
- **Private by design** — conversations live in the browser's `localStorage`; the server keeps no chat history and logs no prompt content.
- **Public-use safeguards** — per-IP sliding-window rate limits, request size limits, concurrency caps, input validation, strict CSP, DOMPurify sanitisation, and user-safe upstream errors.
- **Polished UI** — responsive layout, light/dark themes, conversation search, stop/retry/regenerate, copy buttons, keyboard shortcuts, accessibility labels, and a PWA shell for repeat visits.
- **Operational basics** — `/healthz`, `/readyz`, optional `/api/stats`, request IDs, structured access logs, OpenAPI docs, Docker image, and tests.

## Quick start

Requirements: Python 3.12+ and [Ollama](https://ollama.com) (or another OpenAI-compatible server).

```bash
git clone https://github.com/harshikesh888/gpt-project.git
cd gpt-project

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                          # optional: edit models and limits
ollama serve                                  # if it is not already running
ollama pull qwen2.5-coder:7b                  # or any model you prefer

uvicorn main:app --reload --port 8000
```

Open <http://127.0.0.1:8000>. API documentation is available at <http://127.0.0.1:8000/docs>.

> `.env.example` points at `http://localhost:11434/v1` and uses `qwen2.5-coder:7b`. Run `ollama list` to see installed models, then set `FAST_MODEL` and `DEEP_MODEL` to the names you want. A local server needs no API key; set `LLM_API_KEY` only for remote backends.

## How to use

1. Start the server and open <http://127.0.0.1:8000>.
2. Choose a mode in the top bar:
   - **Fast** — low-latency answers for everyday questions (sent as `mode: "fast"`).
   - **Deep** — more room for reasoning on complex questions (sent as `mode: "smart"`).
3. Type a message and press **Enter** to send. Use **Shift+Enter** for a new line. The answer streams in as the model generates it.
4. While a reply is streaming, press **Stop** to cancel it. Use **Retry** or **Regenerate** to run the prompt again, and the copy button on code blocks to copy snippets.
5. Start a new conversation with **New chat** or **Ctrl+K**. Earlier chats appear in the sidebar and can be searched. They are saved only in your browser's `localStorage`; the server does not keep chat history.
6. Use the theme button to switch between light, dark, and system themes.

Fast mode uses `FAST_MODEL`; Deep mode uses `DEEP_MODEL`. Point both at the same model if you have only one installed.

### Verify that it works

```bash
curl -N http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Say hello in one sentence.","history":[],"mode":"fast"}'
```

`event: delta` lines mean the app is streaming from the model. `GET /readyz` returns `{"status":"ready"}` when the active configuration can serve requests; a `503` means a model or API key is missing.

## API

### `POST /api/chat`

Streams named SSE events.

```bash
curl -N http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "Explain HTTP caching in two sentences.",
    "history": [],
    "mode": "fast"
  }'
```

| Field | Type | Notes |
| --- | --- | --- |
| `message` | string | Required. 1 to `MAX_MESSAGE_CHARS` characters. |
| `history` | array | Optional. Up to 40 `{role, content}` messages; the server trims by count and character budget. |
| `mode` | `fast` or `smart` | `fast` uses `FAST_MODEL`; `smart` uses `DEEP_MODEL`. |

Event stream:

```text
event: meta
data: {"request_id":"...","model":"...","mode":"fast","cached":false}

event: status
data: {"state":"reasoning"}

event: delta
data: {"text":"HTTP caching stores "}

event: done
data: {"request_id":"...","finish_reason":"stop","ttft_ms":412.3,"elapsed_ms":2310.7,"usage":{...}}
```

An `error` event carries a safe, human-readable message and the request ID. HTTP-level failures use a consistent JSON shape:

```json
{
  "error": {
    "code": "rate_limited",
    "message": "Too many requests. Please wait a moment and try again.",
    "request_id": "b4d0c8f2a1e34f67"
  }
}
```

### Other endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /healthz` | Liveness probe with uptime. |
| `GET /readyz` | Readiness; `503` when no model/key configuration is usable. |
| `GET /api/config` | Public client configuration (no secrets). |
| `GET /api/stats` | Optional counters; enable with `STATS_ENABLED=true`. |
| `GET /docs` | OpenAPI/Swagger UI. Disable with `DOCS_ENABLED=false`. |

## Configuration

All settings are environment variables. Copy `.env.example` to `.env` and edit it, or set the variables in your deployment platform.

| Variable | Default | Description |
| --- | --- | --- |
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible upstream. `OLLAMA_BASE_URL` and `HF_BASE_URL` are also accepted. |
| `LLM_API_KEY` | empty | Bearer token for remote backends. Not needed for localhost. `HF_API_KEY`/`HF_TOKEN` still work. |
| `FAST_MODEL` | `qwen2.5-coder:7b` | Low-latency model for fast mode. `OLLAMA_MODEL` and legacy `MODEL` are honored as fallbacks. |
| `DEEP_MODEL` | `qwen2.5-coder:7b` | Reasoning model for deep mode. |
| `PROVIDER` | empty | Optional router-provider field. Ignored by Ollama; set `novita` for the Hugging Face router. |
| `FALLBACK_MODEL` | empty | Extra model to try before the mode's alternate. |
| `FAST_MAX_TOKENS` / `DEEP_MAX_TOKENS` | `700` / `1500` | Output budgets; lower is faster. |
| `FAST_TEMPERATURE` / `DEEP_TEMPERATURE` | `0.5` / `0.6` | Sampling temperatures. |
| `MAX_MESSAGE_CHARS` | `4000` | User message limit; also enforced in the UI. |
| `MAX_HISTORY_MESSAGES` | `12` | Maximum prior messages sent upstream. |
| `MAX_HISTORY_CHARS` | `12000` | Character budget for prior turns. |
| `RATE_LIMIT_REQUESTS` | `30` | Requests per window per client. Set `0` to disable. |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate-limit window. |
| `RATE_LIMIT_MAX_CLIENTS` | `20000` | Upper bound on tracked client keys. |
| `MAX_CONCURRENT_STREAMS` | `32` | Concurrent upstream generations per process. |
| `CONCURRENCY_WAIT_SECONDS` | `2` | How long a request waits for a free stream slot. |
| `REQUEST_BODY_LIMIT` | `65536` | Maximum JSON body size in bytes. |
| `CACHE_ENABLED` | `true` | Exact-match cache for short, context-free fast prompts. |
| `CACHE_TTL_SECONDS` / `CACHE_MAX_ENTRIES` | `900` / `256` | Cache lifetime and entry bound. |
| `CACHE_MAX_CHARS` | `6000` | Answers longer than this are not cached. |
| `TRUST_PROXY_HEADERS` | `false` | Trust `X-Forwarded-For` from a known reverse proxy. |
| `ALLOWED_ORIGINS` | empty | Comma-separated CORS origins for non-browser API clients. |
| `CONNECT_TIMEOUT` / `READ_TIMEOUT` | `10` / `120` | Upstream timeouts in seconds. |
| `WRITE_TIMEOUT` / `POOL_TIMEOUT` | `30` / `5` | Upstream write and connection-pool timeouts. |
| `APP_NAME` / `APP_VERSION` | `Jarvis AI` / `2.0.0` | Branding used by the UI, logs, and OpenAPI docs. |
| `ENVIRONMENT` | `production` | Deployment label for operators. |
| `LOG_LEVEL` | `INFO` | Python log level. |
| `DOCS_ENABLED` | `true` | Expose `/docs` and `/openapi.json`. |
| `STATS_ENABLED` | `false` | Expose `/api/stats`. |

## Performance design

| Technique | Where | Effect |
| --- | --- | --- |
| Shared `httpx.AsyncClient` with keep-alive | `app/llm.py` | Avoids a TLS handshake per message. |
| Async streaming with a bounded queue | `app/main.py` | Does not block the event loop and applies backpressure. |
| Fast/Deep model routing + `max_tokens` caps | `app/config.py`, UI | Shorter, cheaper, lower-latency responses. |
| Exact-match cache | `app/cache.py` | Removes an upstream round-trip for repeated questions. |
| Reasoning separation | `app/llm.py`, `app/text.py` | Hidden chain-of-thought never reaches the browser. |
| Local vendor assets, no CDNs (chat UI) | `static/` | Fewer DNS/TLS round-trips and no third-party runtime requests. |
| Immutable versioned static assets | `app/main.py`, middleware | Repeat visits are served from browser/SW cache. |
| GZip for text assets | `GZipMiddleware` | Smaller HTML/CSS/JS payloads; SSE is excluded automatically. |
| Throttled Markdown rendering | `static/app.js` | Prevents O(n²) DOM work during token streaming. |
| Lazy syntax highlighting | `static/app.js` | The ~130 KB highlighter is only fetched when code appears. |
| PWA app-shell cache | `static/sw.js` | Instant repeat loads and offline shell. |

The app emits `ttft_ms` (time to first token), `elapsed_ms`, token usage, and cache status in logs and SSE metadata, so latency regressions are easy to spot.

## Security and privacy

- The API key, when one is configured, is only read on the server. It is never sent to the browser.
- The server does **not** persist conversations. History is stored in the browser and sent with each request only to provide context.
- Prompt content and IP addresses are not written to application logs. Request IDs are logged instead.
- Rate-limit keys are salted hashes of client IPs. The in-memory limiter keeps at most `RATE_LIMIT_MAX_CLIENTS` entries.
- The browser client renders model output through DOMPurify with raw HTML, images, forms, styles, and event handlers removed. A strict CSP is applied to the chat app.
- Upstream error bodies are logged server-side but never forwarded to users.
- Set `TRUST_PROXY_HEADERS=true` only when a trusted proxy overwrites `X-Forwarded-For`; otherwise clients can spoof the header and bypass per-IP limits.
- Response caching stores short answer text in process memory for up to 15 minutes. Disable with `CACHE_ENABLED=false` if your policy forbids all server-side answer retention.

Please report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## Deployment

The app talks to any OpenAI-compatible endpoint. The default `http://localhost:11434/v1` only works when Ollama runs on the same machine (or in the same container); for hosted platforms, point `LLM_BASE_URL` at a reachable server and set `LLM_API_KEY` if that server requires one.

### Docker

```bash
docker build -t jarvis-ai .
docker run --rm -p 8000:8000 --env-file .env jarvis-ai
```

### Azure App Service

Deploy the repository or the Docker image above, then configure the app:

1. Add `LLM_BASE_URL` and, if required, `LLM_API_KEY` under **App Service → Configuration → Application settings**.
2. Set `TRUST_PROXY_HEADERS=true` and `WEBSITES_PORT=8000`.
3. Enable **SCM_DO_BUILD_DURING_DEPLOYMENT** so Oryx installs `requirements.txt` (skip this when deploying the Docker image).
4. Keep the app at **one worker**. The cache and rate limiter are in-process.

Use this startup command:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1 \
  --no-access-log --no-server-header
```

Enable **Always On** in App Service so the first request does not pay a cold-start penalty.

If you prefer to use Azure's default Gunicorn startup instead, the bundled `gunicorn.conf.py` selects Uvicorn's ASGI worker and keeps the app at one worker.

### Heroku / Render / Railway

Use the `Procfile` or this start command:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1 \
  --no-access-log --no-server-header
```

Set `LLM_BASE_URL` (and `LLM_API_KEY` if the backend needs one) plus `TRUST_PROXY_HEADERS=true` in the platform dashboard.

### Scaling past one instance

The rate limiter, response cache, and concurrency semaphore are intentionally in-process for zero-dependency deployment. For multiple instances:

1. Move rate limiting and caching to Redis.
2. Make cache keys and limits consistent across instances.
3. Remove the single-worker constraint and add a shared health-check strategy.

## Development

```bash
make install     # create .venv and install dev dependencies
make run         # uvicorn with reload on :8000
make test        # pytest
make lint        # ruff
```

Tests use a mocked OpenAI-compatible transport and cover streaming parsing, reasoning filtering, model fallback, caching, rate limiting, validation, size limits, and security headers. No network access, model, or API key is needed.

Run `make lint` and `make test` before opening a pull request.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Page loads but replies fail | Ollama is not running or `LLM_BASE_URL` is wrong | Run `ollama serve`; check `curl http://localhost:11434/v1/models`. |
| `503` from `/readyz` | A remote backend is configured without an API key, or no model is set | Set `LLM_API_KEY` and `FAST_MODEL`/`DEEP_MODEL`. |
| `model not found` from the upstream | The configured model has not been pulled | Run `ollama list`, then `ollama pull <model>` and update `.env`. |
| First reply is very slow | Ollama is loading the model into memory | Wait for the first load, or choose a smaller model. |
| `429 Too Many Requests` | Per-IP rate limit reached | Wait for the window to pass or raise `RATE_LIMIT_REQUESTS`. |
| Long chats get truncated | Ollama's context window is small | Start Ollama with a larger window, e.g. `OLLAMA_CONTEXT_LENGTH=8192 ollama serve`. |

## Project structure

```text
app/
  main.py        FastAPI factory, routes, SSE orchestration
  config.py      Environment settings
  schemas.py     Request validation and normalisation
  prompt.py      System prompts and history budgeting
  llm.py         Async OpenAI-compatible client, SSE parser, fallback
  cache.py       Bounded exact-match response cache
  ratelimit.py   Sliding-window limiter
  text.py        Streaming-safe <think> filter
  middleware.py  Request IDs, body limits, security headers, access logs
  sse.py         SSE serialisation
  stats.py       Optional runtime counters
static/
  app.js         Chat client, SSE parsing, local history, UI
  styles.css     Theme-aware design system
  sw.js          PWA app-shell cache
  vendor/        Vendored marked, DOMPurify, highlight.js
templates/
  index.html     Server-rendered shell
tests/
  test_app.py    API and streaming tests
main.py          ASGI compatibility entrypoint (main:app)
gunicorn.conf.py Single-worker Gunicorn fallback using UvicornWorker
Dockerfile
Procfile
```

## License

MIT — see [LICENSE](LICENSE). Vendored browser libraries and their licenses are
listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
