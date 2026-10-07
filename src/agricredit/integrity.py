"""HMAC tags on server-produced evidence and assessments.

agricredit-mcp signs every evidence item and assessment it emits. The
draft_credit_advisory tool only accepts items whose tags verify, so neither the
LLM nor a tampered agent step can inject fabricated figures or evidence into an
advisory. The key is local to the deployment (env var or a gitignored file).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from .config import REPO_ROOT


def _key() -> bytes:
    env = os.getenv("AGRICREDIT_INTEGRITY_KEY")
    if env:
        return env.encode()
    p = REPO_ROOT / "data" / ".integrity_key"
    if not p.exists():
        p.write_text(secrets.token_hex(32))
        p.chmod(0o600)
    return p.read_text().strip().encode()


def _canon(obj: dict) -> bytes:
    body = {k: v for k, v in obj.items() if k != "integrity"}
    return json.dumps(body, sort_keys=True, default=str).encode()


def sign(obj: dict) -> str:
    return hmac.new(_key(), _canon(obj), hashlib.sha256).hexdigest()


def verify(obj: dict) -> bool:
    tag = obj.get("integrity")
    return isinstance(tag, str) and hmac.compare_digest(tag, sign(obj))


def signed(model):
    """Return a copy of a pydantic model with its `integrity` field set over its JSON form."""
    data = model.model_dump(mode="json")
    return model.model_copy(update={"integrity": sign(data)})
