"""Officer accounts for the hosted dashboard.

Each account links a username to a registered officer (the `officers` table), so every approval
recorded through the dashboard carries the name of the person who is signed in. Passwords are
never stored: only a salted PBKDF2-SHA256 hash (Python standard library, 600,000 iterations).

Accounts live in a JSON file outside the code (AGRICREDIT_USERS_FILE, default data/users.json),
on the persistent volume in Docker. Manage them with:

    python -m agricredit.auth add USERNAME --officer-id OFF-004 --name "A. Officer"   # asks for a password
    python -m agricredit.auth passwd USERNAME
    python -m agricredit.auth disable USERNAME
    python -m agricredit.auth list

A hosted demo can create accounts at start-up from a secret, without any password in the code:

    AGRICREDIT_BOOTSTRAP_USERS="judge:<password>:OFF-001,officer2:<password>:OFF-002"
    python -m agricredit.auth bootstrap
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

from . import db
from .config import REPO_ROOT

ITERATIONS = 600_000
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
OFFICER_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,31}$")
MIN_PASSWORD = 10


class AuthError(ValueError):
    pass


def auth_required() -> bool:
    return os.getenv("AGRICREDIT_AUTH_REQUIRED", "false").strip().lower() in {"1", "true", "yes", "on"}


def users_file() -> Path:
    raw = os.getenv("AGRICREDIT_USERS_FILE")
    p = Path(raw) if raw else REPO_ROOT / "data" / "users.json"
    return p if p.is_absolute() else (REPO_ROOT / p)


def _load() -> dict:
    p = users_file()
    if not p.exists():
        return {"users": {}}
    return json.loads(p.read_text())


def _save(data: dict) -> None:
    p = users_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".users.")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS).hex()


def _check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"password must have at least {MIN_PASSWORD} characters")


def ensure_officer(officer_id: str, officer_name: str | None, role: str = "extension_officer") -> dict:
    """Return the registered officer, creating it when a name is given and it does not exist yet."""
    if not OFFICER_ID_RE.match(officer_id):
        raise AuthError("officer id must look like OFF-004 (capital letters, digits and dashes)")
    off = db.get_officer(officer_id)
    if off:
        return off
    if not officer_name:
        raise AuthError(f"officer {officer_id} is not registered; give --name to register them")
    with db.write_conn() as c:
        c.execute("INSERT OR REPLACE INTO officers VALUES (?,?,?,1)", (officer_id, officer_name, role))
    return db.get_officer(officer_id)


def add_user(username: str, password: str, officer_id: str, officer_name: str | None = None,
             *, overwrite: bool = False) -> dict:
    username = username.strip().lower()
    if not USERNAME_RE.match(username):
        raise AuthError("username: 2 to 32 characters, lower-case letters, digits, dot, dash or underscore")
    _check_password_strength(password)
    off = ensure_officer(officer_id, officer_name)
    data = _load()
    if username in data["users"] and not overwrite:
        raise AuthError(f"user {username} already exists (use passwd to change the password)")
    salt = secrets.token_bytes(16)
    data["users"][username] = {"officer_id": off["officer_id"], "salt": salt.hex(), "hash": _hash(password, salt),
                               "iterations": ITERATIONS, "active": True}
    _save(data)
    return {"username": username, **off}


def set_password(username: str, password: str) -> None:
    _check_password_strength(password)
    data = _load()
    u = data["users"].get(username)
    if not u:
        raise AuthError(f"no user {username}")
    salt = secrets.token_bytes(16)
    u.update(salt=salt.hex(), hash=_hash(password, salt), iterations=ITERATIONS)
    _save(data)


def set_active(username: str, active: bool) -> None:
    data = _load()
    if username not in data["users"]:
        raise AuthError(f"no user {username}")
    data["users"][username]["active"] = active
    _save(data)


def authenticate(username: str, password: str) -> dict | None:
    """Return the signed-in officer ({username, officer_id, officer_name, role}) or None."""
    username = (username or "").strip().lower()
    u = _load()["users"].get(username)
    # Hash even for unknown users, so response time does not reveal which usernames exist.
    salt = bytes.fromhex(u["salt"]) if u else b"\x00" * 16
    digest = hashlib.pbkdf2_hmac("sha256", (password or "").encode(), salt, (u or {}).get("iterations", ITERATIONS)).hex()
    if not u or not u.get("active") or not hmac.compare_digest(digest, u["hash"]):
        return None
    off = db.get_officer(u["officer_id"])
    if not off:
        return None
    return {"username": username, **off}


def bootstrap_from_env() -> list[str]:
    """Create or update the accounts listed in AGRICREDIT_BOOTSTRAP_USERS (username:password:OFFICER-ID,...)."""
    raw = os.getenv("AGRICREDIT_BOOTSTRAP_USERS", "").strip()
    made = []
    for item in filter(None, (x.strip() for x in raw.split(","))):
        parts = item.split(":")
        if len(parts) != 3:
            raise AuthError("AGRICREDIT_BOOTSTRAP_USERS entries must be username:password:OFFICER-ID")
        username, password, officer_id = parts
        add_user(username, password, officer_id, overwrite=True)
        made.append(username.strip().lower())
    return made


def list_users() -> list[dict]:
    return [{"username": k, "officer_id": v["officer_id"], "active": v["active"]}
            for k, v in sorted(_load()["users"].items())]


def _ask_password() -> str:
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Repeat password: "):
        raise AuthError("the two passwords are different")
    return pw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agricredit.auth", description="Manage officer accounts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="create an account (asks for the password)")
    a.add_argument("username")
    a.add_argument("--officer-id", required=True, help="for example OFF-004")
    a.add_argument("--name", help="officer's full name; registers the officer if the id is new")
    for name in ("passwd", "disable", "enable"):
        sub.add_parser(name).add_argument("username")
    sub.add_parser("list")
    sub.add_parser("bootstrap", help="create accounts from AGRICREDIT_BOOTSTRAP_USERS")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "add":
            u = add_user(args.username, _ask_password(), args.officer_id, args.name)
            print(f"created {u['username']} -> {u['officer_id']} ({u['officer_name']})")
        elif args.cmd == "passwd":
            set_password(args.username, _ask_password())
            print("password changed")
        elif args.cmd in {"disable", "enable"}:
            set_active(args.username, args.cmd == "enable")
            print(f"{args.username} {args.cmd}d")
        elif args.cmd == "list":
            for u in list_users():
                print(f"{u['username']:<20} {u['officer_id']:<10} {'active' if u['active'] else 'disabled'}")
        elif args.cmd == "bootstrap":
            made = bootstrap_from_env()
            print(f"bootstrap: {len(made)} account(s) ready" + (f": {', '.join(made)}" if made else ""))
    except AuthError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
