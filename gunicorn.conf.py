"""Gunicorn fallback configuration.

Some platforms default to `gunicorn main:app`. The ASGI app needs Uvicorn's
worker class, and the in-process cache/rate limiter require exactly one worker.
The primary start commands use Uvicorn directly; this file keeps the fallback
safe and consistent.
"""

from __future__ import annotations

import os

bind = f"0.0.0.0:{int(os.getenv('PORT', '8000'))}"
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 120
graceful_timeout = 30
keepalive = 5
accesslog = None
errorlog = "-"
loglevel = "info"
