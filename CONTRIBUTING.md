# Contributing

Thanks for improving Jarvis AI.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env .env   # points at Ollama on localhost by default
uvicorn main:app --reload
```

## Before opening a pull request

```bash
ruff check .
pytest
```

Both commands must pass. Tests do not call the network.

## Guidelines

- Keep the chat client framework-free and free of a build step; vendor new browser
  libraries under `static/vendor/` and add them to the asset-version hash.
- Do not log message content, API keys, or raw client IP addresses.
- Preserve the strict Content Security Policy for the chat app. If a dependency
  cannot work under it, discuss the trade-off first.
- Add a test for behavioural changes in streaming, validation, caching, or rate
  limiting.
- Keep the deployment model single-worker unless the shared-state limitation is
  addressed.
