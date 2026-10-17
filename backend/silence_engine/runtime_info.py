"""Actual runtime facts about the host, read from the interpreter and kernel.

The deployment label is whatever the operator configured; the architecture
fields come from ``platform`` and ``/proc/sysinfo`` (present on Linux on IBM Z)
and are reported as found. Nothing here asserts hardware that was not read.
"""

from __future__ import annotations

import os
import platform
import socket
import sqlite3
import sys
import time
from importlib import metadata
from pathlib import Path
from typing import Dict, Optional

_STARTED = time.time()
SYSINFO_KEYS = ("Manufacturer", "Type", "Model", "Model Capacity", "VM00 Control Program")


def _pkg_version(name: str) -> Optional[str]:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _os_release() -> Optional[str]:
    path = Path("/etc/os-release")
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def _sysinfo() -> Optional[Dict[str, str]]:
    """Selected, non-sensitive fields from /proc/sysinfo (Linux on IBM Z only)."""
    path = Path("/proc/sysinfo")
    if not path.is_file():
        return None
    found: Dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            if key in SYSINFO_KEYS and key not in found:
                found[key] = " ".join(value.split())
    except OSError:
        return None
    return found or None


def collect(deployment_label: Optional[str] = None) -> dict:
    machine = platform.machine()
    sysinfo = _sysinfo()
    is_s390x = machine == "s390x"
    if is_s390x and sysinfo and sysinfo.get("Manufacturer", "").upper().startswith("IBM"):
        verdict = "Verified: s390x architecture, and /proc/sysinfo reports an IBM machine."
    elif is_s390x:
        verdict = "Verified: s390x architecture (IBM Z / LinuxONE instruction set). /proc/sysinfo not readable."
    else:
        verdict = f"Not running on IBM Z: this host reports the '{machine}' architecture."
    return {
        "system": platform.system(),
        "release": platform.release(),
        "os_pretty_name": _os_release() or (f"macOS {platform.mac_ver()[0]}" if platform.system() == "Darwin" else None),
        "machine": machine,
        "processor": platform.processor() or None,
        "byteorder": sys.byteorder,
        "cpu_count": os.cpu_count(),
        "hostname": socket.gethostname(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_compiler": platform.python_compiler(),
        "sqlite_version": sqlite3.sqlite_version,
        "flask_version": _pkg_version("flask"),
        "werkzeug_version": _pkg_version("werkzeug"),
        "gunicorn_version": _pkg_version("gunicorn"),
        # Optional ML stack, read from package metadata (nothing is imported). None = not installed.
        "ml_packages": {name: _pkg_version(name) for name in
                        ("scikit-learn", "numpy", "scipy", "pandas", "xgboost", "torch", "tensorflow")},
        "pid": os.getpid(),
        "uptime_seconds": round(time.time() - _STARTED, 1),
        "is_s390x": is_s390x,
        "ibm_z_sysinfo": sysinfo,
        "architecture_verdict": verdict,
        "deployment_label": deployment_label,
        "deployment_label_note": "Operator-configured text (SILENCE_DEPLOYMENT_LABEL); not verified by the application."
        if deployment_label else None,
    }


def brief() -> dict:
    """Small subset stored with each analysis run for provenance."""
    return {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version(),
            "byteorder": sys.byteorder, "hostname": socket.gethostname()}
