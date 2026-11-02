FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app app ./app
COPY --chown=app:app static ./static
COPY --chown=app:app templates ./templates
COPY --chown=app:app main.py ./
COPY --chown=app:app gunicorn.conf.py ./
COPY --chown=app:app LICENSE THIRD_PARTY_NOTICES.md ./

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"

# One worker is intentional: the rate limiter, response cache, and concurrency
# guard are in-process. Scale horizontally only after moving them to Redis.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log --no-server-header"]
