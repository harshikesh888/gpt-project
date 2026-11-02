# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/harshikesh888/gpt-project/security/advisories/new)
and include:

- a description and impact assessment,
- reproduction steps or a proof of concept,
- the affected endpoint or version.

You can expect an acknowledgement within a few days. Please allow time for a fix
before public disclosure.

## Deployment checklist

- Store `LLM_API_KEY` in the platform's secret manager, never in the repository. A local Ollama server needs no key.
- Serve the app over HTTPS.
- Set `TRUST_PROXY_HEADERS=true` only behind a proxy that overwrites
  `X-Forwarded-For`; otherwise leave it `false`.
- Keep `RATE_LIMIT_REQUESTS` and `MAX_CONCURRENT_STREAMS` appropriate for your
  upstream quota.
- Disable `/docs` (`DOCS_ENABLED=false`) if you do not want a public API schema.
- Keep `STATS_ENABLED=false` unless you intentionally expose runtime counters.
- Rotate the provider API key if it may have been exposed.
- Review the provider's data-retention policy; prompts are sent to the upstream
  model provider, even though this app does not persist conversations.

## Scope

This is a small single-process application. The in-memory rate limiter and cache
are per instance. Running multiple instances without a shared limiter weakens
abuse protection and should be done only after moving that state to a shared
store such as Redis.
