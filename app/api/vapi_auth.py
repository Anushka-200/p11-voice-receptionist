"""Every request from Vapi must carry our shared secret. Anything else is rejected before any work is done.

Analogy: the guard at the door checks the badge on every single visitor, not just the first one.
Why constant-time compare: a normal `==` stops at the first wrong character, and the tiny timing
difference can leak the secret one character at a time.
"""

import hmac

from fastapi import Header, HTTPException

from app.settings import get_settings


def require_vapi_secret(x_vapi_secret: str | None = Header(default=None)) -> None:
    expected = get_settings().vapi_webhook_secret.get_secret_value()
    # An empty configured secret means "not set up": reject everything rather than accept everything.
    if not expected or not x_vapi_secret or not hmac.compare_digest(x_vapi_secret, expected):
        raise HTTPException(status_code=401, detail="invalid or missing secret")
