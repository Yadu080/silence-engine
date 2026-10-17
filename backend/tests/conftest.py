from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from silence_engine import data_generator, ingest, service
from silence_engine.app import create_app
from silence_engine.config import load_settings
from silence_engine.ingest import Observation


def make_obs(stream: str, d: date, count: Optional[int], *, open_: bool = True, heartbeat: Optional[bool] = True,
             batches: Optional[int] = 8, received: Optional[int] = None, last_seen: Optional[datetime] = None,
             weather: int = 0, transport: bool = False, outage: bool = False, status: Optional[str] = None,
             delay_days: int = 0) -> Observation:
    site, svc = stream.split(":")
    seen = last_seen or datetime.combine(d, time(18, 0), tzinfo=timezone.utc)
    return Observation(
        site_id=site, service_type=svc, service_date=d, observed_count=count,
        value_status=status or ("ok" if count is not None else "missing"), scheduled_open=open_,
        feed_heartbeat_received=heartbeat, feed_last_seen=seen,
        report_received_at=datetime.combine(d + timedelta(days=delay_days), time(19, 0), tzinfo=timezone.utc),
        batches_expected=batches if open_ else 0, batches_received=(received if received is not None else batches) if open_ else 0,
        weather_severity=weather, transport_disruption=transport, reported_outage=outage, source="test")


def steady_stream(stream: str, start: date, days: int, level: int = 100, weekend_closed: bool = True,
                  wiggle: int = 6) -> List[Observation]:
    """Deterministic, mildly noisy series with weekends closed."""
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        closed = weekend_closed and d.weekday() >= 5
        value = 0 if closed else level + ((i * 7) % (2 * wiggle + 1)) - wiggle
        out.append(make_obs(stream, d, value, open_=not closed))
    return out


@pytest.fixture(scope="session")
def demo_dataset():
    return data_generator.generate(seed=42)


@pytest.fixture(scope="session")
def demo_observations(demo_dataset):
    return ingest.validate_records(demo_dataset.raw_records, source="synthetic").observations


def _settings(tmp: Path, **extra: str):
    env: Dict[str, str] = {
        "SILENCE_DATA_DIR": str(tmp),
        "SILENCE_AUTO_INIT_DEMO": "0",
        "SILENCE_EVAL_REPLAY": "0",
        "SILENCE_FRONTEND_DIST": str(tmp / "no-frontend"),
        "SILENCE_LOG_LEVEL": "WARNING",
    }
    env.update(extra)
    return load_settings(env)


@pytest.fixture(scope="session")
def initialised_settings(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("silence-data")
    settings = _settings(tmp)
    service.generate_demo(settings, seed=42, actor="test")
    return settings


@pytest.fixture()
def client(initialised_settings):
    app = create_app(initialised_settings, start_background=False)
    app.testing = True
    return app.test_client()


@pytest.fixture()
def make_settings(tmp_path):
    return lambda **extra: _settings(tmp_path, **extra)
