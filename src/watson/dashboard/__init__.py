"""FastAPI app, local auth, templates, and static assets."""

from watson.dashboard.app import create_app
from watson.dashboard.bind import ALLOWED_BIND_HOST, BindError, validate_bind_host
from watson.dashboard.serve import run_dashboard
from watson.dashboard.tokens import TokenError, get_token_provider, set_token_provider

__all__ = [
    "ALLOWED_BIND_HOST",
    "BindError",
    "TokenError",
    "create_app",
    "get_token_provider",
    "run_dashboard",
    "set_token_provider",
    "validate_bind_host",
]
