"""gunicorn settings for SILENCE ENGINE (Linux incl. s390x, and macOS).

One worker process with several threads: SQLite and the in-process background
job runner are designed for a single process. Bind address and port come from
the same settings the app uses (environment or .env), defaulting to loopback.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from silence_engine.config import load_settings  # noqa: E402

_settings = load_settings()

bind = f"{_settings.host}:{_settings.port}"
workers = 1
worker_class = "gthread"
threads = 4
timeout = 120          # evaluation runs as a background job, so requests stay short
graceful_timeout = 30
keepalive = 5
chdir = str(ROOT / "backend")
accesslog = "-"
errorlog = "-"
loglevel = "info"
# Request line, status and size only: no headers (tokens) and no bodies are logged.
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(L)ss'
