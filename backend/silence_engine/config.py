"""Runtime configuration loaded from environment variables (and an optional .env).

No secrets are hard-coded. ``SILENCE_API_TOKEN`` is optional; when it is unset,
write endpoints only accept requests from the loopback interface.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


def _load_dotenv(path: Path) -> dict:
    """Minimal KEY=VALUE parser so python-dotenv is not a dependency."""
    values: dict = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _bool(value: Optional[str], default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: Optional[str], default: int, lo: int, hi: int) -> int:
    try:
        parsed = int(value) if value not in (None, "") else default
    except ValueError:
        parsed = default
    return max(lo, min(hi, parsed))


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    labels_path: Path
    frontend_dist: Path
    host: str
    port: int
    deployment_label: Optional[str]
    api_token: Optional[str]
    auto_init_demo: bool
    demo_seed: int
    log_level: str
    max_upload_mb: int
    max_import_rows: int
    eval_replay: bool = True
    cors_origins: Tuple[str, ...] = ()
    csp: Optional[str] = None
    ml_triage: bool = True

    def describe(self) -> dict:
        """Safe-to-display summary (never includes the token)."""
        return {
            "data_dir": str(self.data_dir),
            "db_path": str(self.db_path),
            "frontend_dist": str(self.frontend_dist),
            "host": self.host,
            "port": self.port,
            "deployment_label": self.deployment_label,
            "api_token_configured": bool(self.api_token),
            "auto_init_demo": self.auto_init_demo,
            "demo_seed": self.demo_seed,
            "max_upload_mb": self.max_upload_mb,
            "max_import_rows": self.max_import_rows,
            "eval_replay": self.eval_replay,
            "ml_triage": self.ml_triage,
            "cors_origins": list(self.cors_origins),
            "csp_overridden": bool(self.csp),
        }


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    if env is None:
        merged = dict(_load_dotenv(PROJECT_ROOT / ".env"))
        merged.update(os.environ)  # real environment wins over .env
        env = merged

    def get(key: str, default: Optional[str] = None) -> Optional[str]:
        return env.get(key, default)

    def path(value: Optional[str], default: Path) -> Path:
        # Relative paths are resolved against the project root, not the process CWD,
        # so the same .env works under the dev server, gunicorn and systemd.
        p = Path(value).expanduser() if value else default
        return (p if p.is_absolute() else PROJECT_ROOT / p).resolve()

    data_dir = path(get("SILENCE_DATA_DIR"), PROJECT_ROOT / "data")
    db_path = path(get("SILENCE_DB_PATH"), data_dir / "silence_engine.sqlite3")
    labels_path = path(get("SILENCE_LABELS_PATH"), data_dir / "synthetic" / "ground_truth_labels.json")
    frontend_dist = path(get("SILENCE_FRONTEND_DIST"), PROJECT_ROOT / "frontend" / "dist")
    label = (get("SILENCE_DEPLOYMENT_LABEL") or "").strip() or None
    token = (get("SILENCE_API_TOKEN") or "").strip() or None

    return Settings(
        data_dir=data_dir,
        db_path=db_path,
        labels_path=labels_path,
        frontend_dist=frontend_dist,
        host=get("SILENCE_HOST") or "127.0.0.1",
        port=_int(get("SILENCE_PORT"), 8000, 1, 65535),
        deployment_label=label[:80] if label else None,
        api_token=token,
        auto_init_demo=_bool(get("SILENCE_AUTO_INIT_DEMO"), True),
        demo_seed=_int(get("SILENCE_DEMO_SEED"), 42, 0, 2**31 - 1),
        log_level=(get("SILENCE_LOG_LEVEL") or "INFO").upper(),
        max_upload_mb=_int(get("SILENCE_MAX_UPLOAD_MB"), 10, 1, 100),
        max_import_rows=_int(get("SILENCE_MAX_IMPORT_ROWS"), 200_000, 100, 2_000_000),
        eval_replay=_bool(get("SILENCE_EVAL_REPLAY"), True),
        ml_triage=_bool(get("SILENCE_ML_TRIAGE"), True),
        cors_origins=tuple(o.strip().rstrip("/") for o in (get("SILENCE_CORS_ORIGINS") or "").split(",")
                           if o.strip().startswith(("http://", "https://"))),
        csp=(get("SILENCE_CSP") or "").strip() or None,
    )
