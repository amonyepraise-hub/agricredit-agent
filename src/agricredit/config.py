"""Central configuration for AgriCredit Agent.

Everything is read from environment variables (see .env.example) so the same
code runs unmodified in local dev, Docker and CI. No secrets are hard-coded.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

# Fixed "as-of" clock. The synthetic dataset is generated relative to this
# instant, and every freshness/staleness check uses it, so demo and eval runs
# are reproducible regardless of the wall-clock date they are executed on.
AS_OF = dt.datetime.fromisoformat(os.getenv("AGRICREDIT_AS_OF", "2026-10-07T09:00:00"))


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _path(name: str, default: Path) -> str:
    raw = os.getenv(name)
    if not raw:
        return str(default)
    p = Path(raw)
    return str(p if p.is_absolute() else (REPO_ROOT / p).resolve())


@dataclass(frozen=True)
class Settings:
    llm_backend: str = field(default_factory=lambda: os.getenv("AGRICREDIT_LLM_BACKEND", "ollama"))
    ollama_base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct"))
    openai_compat_base_url: str = field(default_factory=lambda: os.getenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:8000/v1"))
    openai_compat_model: str = field(default_factory=lambda: os.getenv("OPENAI_COMPATIBLE_MODEL", "Qwen/Qwen2.5-7B-Instruct"))
    openai_compat_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_COMPATIBLE_API_KEY", "not-needed"))
    llm_timeout_s: float = field(default_factory=lambda: _float("AGRICREDIT_LLM_TIMEOUT_S", 180.0))

    db_path: str = field(default_factory=lambda: _path("AGRICREDIT_DB_PATH", REPO_ROOT / "data" / "agricredit.db"))
    log_dir: str = field(default_factory=lambda: _path("AGRICREDIT_LOG_DIR", REPO_ROOT / "logs"))
    risk_model_path: str = field(default_factory=lambda: _path("AGRICREDIT_RISK_MODEL_PATH", REPO_ROOT / "data" / "risk_model.joblib"))
    fs_mcp_root: str = field(default_factory=lambda: _path("AGRICREDIT_FS_MCP_ROOT", REPO_ROOT / "fs_mcp_root"))
    # Language of the advisory explanation: "en" or "fr" (Benin's extension services work in French).
    advisory_language: str = field(default_factory=lambda: os.getenv("AGRICREDIT_ADVISORY_LANGUAGE", "en").strip().lower()[:2])
    # Policy file name inside <fs_mcp_root>/policy/. The real-open-data mode uses agent_policy_real.json.
    policy_file: str = field(default_factory=lambda: os.getenv("AGRICREDIT_POLICY_FILE", "agent_policy.json"))

    # Freshness limits (days). Defaults suit the synthetic weekly feeds; real WFP prices are monthly and
    # published with a lag, so the real-open-data mode raises the market limit (see run_demo.sh --real).
    repayment_freshness_days: float = field(default_factory=lambda: _float("AGRICREDIT_REPAYMENT_FRESHNESS_DAYS", 30.0))
    market_freshness_days: float = field(default_factory=lambda: _float("AGRICREDIT_MARKET_FRESHNESS_DAYS", 14.0))
    weather_freshness_days: float = field(default_factory=lambda: _float("AGRICREDIT_WEATHER_FRESHNESS_DAYS", 7.0))

    yield_shock_pct: float = field(default_factory=lambda: _float("AGRICREDIT_YIELD_SHOCK_PCT", 0.20))
    price_shock_pct: float = field(default_factory=lambda: _float("AGRICREDIT_PRICE_SHOCK_PCT", 0.10))

    model_version_approval_sensitive: bool = field(
        default_factory=lambda: _bool("AGRICREDIT_MODEL_VERSION_APPROVAL_SENSITIVE", True)
    )
    approval_ttl_hours: float = field(default_factory=lambda: _float("AGRICREDIT_APPROVAL_TTL_HOURS", 72.0))

    # Comma-separated fault-injection flags, used ONLY by the evaluation suite
    # and the demo's "deliberate data problem" step. Example:
    #   AGRICREDIT_FAULTS=weather_unavailable:BEN-LOC-03
    faults: str = field(default_factory=lambda: os.getenv("AGRICREDIT_FAULTS", ""))

    @property
    def model_version(self) -> str:
        if self.llm_backend == "ollama":
            return f"ollama:{self.ollama_model}"
        if self.llm_backend == "openai_compatible":
            return f"openai_compatible:{self.openai_compat_model}"
        return "offline-stub:deterministic-v1 (NOT an LLM)"

    @property
    def is_open_weight_backend(self) -> bool:
        return self.llm_backend in {"ollama", "openai_compatible"}


def get_settings() -> Settings:
    """Settings are re-read on each call so eval runs can switch env vars."""
    return Settings()
