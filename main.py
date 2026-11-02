"""ASGI entrypoint.

Kept at the repository root so existing Azure App Service, Uvicorn, and
Gunicorn start commands that reference ``main:app`` continue to work.
The application itself lives in :mod:`app.main`.
"""

from app.main import app

__all__ = ["app"]
