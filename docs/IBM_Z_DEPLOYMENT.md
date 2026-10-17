# Deploying on IBM LinuxONE (s390x)

This guide runs the operational backend — ingestion, validation, feed-health checks, detection, triage, evaluation, the REST API and the SQLite audit database — on a LinuxONE virtual machine, as a normal (non-root) user, reachable only through an SSH tunnel. The dashboard is built on your Mac and shipped as static files, so **Node.js is not needed on the server**.

Status: the steps below were rehearsed end-to-end on macOS (install from the release tarball into a clean directory with a separate Python, tests, demo generation, gunicorn, fingerprint). The notebook route (Parts A and B) and the dashboard were then run on an IBM LinuxONE Community Cloud instance during the datathon. On any machine, the Platform page and `manage.py check-env` show the architecture actually in use.

## 0. Your instance

Set these once in your Mac terminal. Use the values shown on your LinuxONE Community Cloud dashboard (they are deliberately not stored in this repository).

| Item | Example |
|---|---|
| User | `linux1` |
| Public IP | the address shown on your instance page |
| Key | the `.pem` file you downloaded for the instance (must be `chmod 600`) |

```bash
export LXKEY=~/.ssh/your-instance-key.pem
```

```bash
export LXHOST=linux1@<SERVER_IP>
```

## 1. On the Mac: build the release

```bash
./scripts/package_release.sh
```

This builds `frontend/dist`, bundles `backend/`, `frontend/dist/`, `scripts/`, `docs/`, the requirements files and `.env.example` into `release/silence-engine-1.0.0.tar.gz`, and prints its SHA-256. No data, virtualenv or secrets are included.

Record the Mac's reproducibility hashes for later:

```bash
.venv/bin/python backend/manage.py fingerprint
```

Seed 42 gives `raw_synthetic_fingerprint = fd71e981ade10e5747117565c82ac1b6fcf31378c6a38211a0ce80fdd29c63e5` and `evaluation_results_sha256 = e201d75415c8d1aaf34d6af6b85cb28aa788d6f51207acb1e24cac734532ea1d`.

## 2. Copy it to LinuxONE

```bash
scp -i "$LXKEY" release/silence-engine-1.0.0.tar.gz "$LXHOST":~/
```

## 3. On LinuxONE: check the environment (read-only)

```bash
ssh -i "$LXKEY" "$LXHOST"
```

```bash
tar xzf silence-engine-1.0.0.tar.gz && cd silence-engine-1.0.0
```

```bash
./scripts/linuxone/check_env.sh
```

It prints the architecture (expect `s390x`), the OS, `/proc/sysinfo` manufacturer/type, available Python 3.10+ interpreters, `sqlite3`/`venv`/`ssl` availability, whether `gcc` is present, whether port 8000 is free and whether PyPI is reachable. It changes nothing.

If no Python 3.10+ is available, install one with your distribution's packages (needs `sudo`; LinuxONE Community Cloud users normally have it):

| Distribution | Command |
|---|---|
| RHEL 8 / 9 | `sudo dnf install -y python3.11 python3.11-devel gcc` |
| Ubuntu 22.04 / 24.04 | `sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-dev build-essential` |
| SLES 15 | `sudo zypper install -y python311 python311-devel gcc` |

`gcc` and the `-devel` headers are optional (see the compatibility table).

## 4. Install as the normal user

```bash
PYTHON=python3.11 ./scripts/linuxone/install.sh
```

(Omit `PYTHON=` to let it pick the newest Python ≥ 3.10.) The script refuses to run as root. It creates `.venv`, installs the exact tested versions from `requirements-lock.txt` (falling back to the compatible ranges), creates a private `.env` (mode 600) and `data/` (mode 700), **runs the full test suite on s390x**, generates the synthetic demo dataset and prints the reproducibility fingerprints. Add `--skip-tests` to skip the tests.

## 5. Start the backend

```bash
./scripts/linuxone/run.sh --background
```

gunicorn binds to `127.0.0.1:8000` (from `.env`), one worker with four threads, logs to `data/server.log`, pid in `data/server.pid`. Stop it with `./scripts/linuxone/stop.sh`. Foreground mode: `./scripts/linuxone/run.sh`.

Health check on the server:

```bash
curl -s http://127.0.0.1:8000/api/health
```

## 6. Open the dashboard from your Mac through an SSH tunnel

In a new Mac terminal:

```bash
ssh -i "$LXKEY" -N -L 8000:127.0.0.1:8000 "$LXHOST"
```

Then open <http://127.0.0.1:8000>. Because the request reaches gunicorn from the server's loopback interface, write actions (reviews, regenerate, import) work without a token, while nothing is exposed to the internet. The **Platform** page should now read `s390x`, `big-endian`, and show the IBM machine type from `/proc/sysinfo`. If port 8000 is busy on your Mac, use `-L 8080:127.0.0.1:8000` and open port 8080.

Optionally set a label (shown as *unverified*, next to the verified architecture):

```bash
echo 'SILENCE_DEPLOYMENT_LABEL=LinuxONE Community Cloud VM' >> .env && ./scripts/linuxone/stop.sh && ./scripts/linuxone/run.sh --background
```

## 7. Verify the same workload gives the same results

```bash
./scripts/linuxone/verify_reproducibility.sh fd71e981ade10e5747117565c82ac1b6fcf31378c6a38211a0ce80fdd29c63e5 e201d75415c8d1aaf34d6af6b85cb28aa788d6f51207acb1e24cac734532ea1d
```

`MATCH` on both lines means the synthetic dataset and every evaluation metric were reproduced bit-for-bit on s390x (big-endian) as on the Mac (little-endian). The generator uses only Python's Mersenne Twister and IEEE-754 arithmetic, so a mismatch would be worth investigating (for example a different Python minor version changing a library algorithm) and reporting honestly.

Each analysis and evaluation run also records the machine it ran on; the Platform page lists recent runs with their architecture, and the audit log stores the same platform details.

## Alternative: run everything from a Jupyter notebook on LinuxONE

If your LinuxONE access is a Jupyter (JupyterLab/Notebook) instance rather than a shell, do **not** paste source files into cells: the release has around 60 files, including binary font files in the built dashboard. Instead:

1. In Jupyter's file browser, **Upload** two files into the same folder: `release/silence-engine-1.0.0.tar.gz` and `notebooks/SilenceEngine_on_LinuxONE.ipynb` (the notebook is also inside the tarball).
2. Open the notebook and run the cells from top to bottom.
   - **Part A** unpacks the tarball safely, then generates the synthetic data, runs detection, triage and the full evaluation **inside the kernel using only the standard library** (no pip, no internet), printing the processor architecture and checking the results hash against the Mac reference.
   - **Part B** runs `install.sh` (virtualenv, Flask/gunicorn from PyPI, demo database), the full test suite, starts gunicorn in the background on port 8000 and checks `/api/health`. If the kernel can import scikit-learn (as the LinuxONE ML image's kernel can), the venv is built from that interpreter with `--system-site-packages` so the dashboard's ML section works too.
   - **Part C** (optional) trains the scikit-learn triage model in the kernel and prints the ML architecture, the side-by-side comparison with the rule-based triage and the reproducibility hashes (see "Optional: the ML triage benchmark on LinuxONE" below). Without scikit-learn it prints why it was skipped.
3. To see the dashboard:
   - if the Jupyter server has **jupyter-server-proxy**, the notebook prints a link of the form `<jupyter base URL>/proxy/8000/`. Open it in a new tab. The dashboard uses relative URLs, so it works under that prefix (tested locally behind an equivalent prefix-stripping proxy, including write actions);
   - otherwise use the SSH tunnel from step 6 if the notebook runs on a VM you can SSH into.
4. Run the last cell to stop the server when you are done. The server outlives kernel restarts, but not the Jupyter container itself.

Notes: Jupyter images often run as `root`. The notebook then sets `SILENCE_ALLOW_ROOT=1` for the scripts, which otherwise refuse root. Use this only inside a single-user container. If the kernel's Python is older than 3.10, Part A may still work but is untested; `install.sh` looks for a newer `python3.x` for Part B. The notebook (Parts A–C) was executed end-to-end with `jupyter nbconvert --execute` on macOS (arm64) — Part C with scikit-learn 1.7.2 — and has not yet been run on s390x.

## Optional: the ML triage benchmark on LinuxONE

The core engine needs only the standard library. The optional benchmark (`ml_triage.py`, `ml_evaluation.py`) trains a scikit-learn random forest on the synthetic training period and scores it against the rule-based triage on the test period (METHODOLOGY section 9).

**Do not `pip install` numpy or scikit-learn on s390x.** PyPI has no s390x wheels for them (checked 17 Oct 2026), so pip would compile them from source, which needs a full toolchain and a long build. Use the Python of the LinuxONE ML container image (`registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x`), which ships scikit-learn, XGBoost, pandas, TensorFlow and PyTorch built for s390x. Which versions it ships is whatever the image contains; `check_env.sh` and notebook Part C print them.

```bash
./scripts/linuxone/check_env.sh        # lists scikit-learn / numpy / xgboost / pandas / torch / tensorflow per interpreter
```

Ways to run it, from simplest:

1. **Notebook Part C** in the ML image's Jupyter kernel (after Part A). Prints the architecture, library versions, the training set, the comparison with the rules, the feature importances, every disagreement with its reason, and compares the hashes with the Mac reference.
2. **Command line with the image's Python** (no venv needed; `manage.py` imports no Flask except for `serve`):
   ```bash
   python3 backend/manage.py init-demo --if-empty && python3 backend/manage.py evaluate-ml --out ml_report.json
   ```
3. **Dashboard:** create the venv from the image's Python so it can see those packages, then open Evaluation → "Optional: machine-learning triage vs the rules":
   ```bash
   SILENCE_ALLOW_ROOT=1 PYTHON=$(which python3) SILENCE_VENV_SYSTEM_SITE=1 ./scripts/linuxone/install.sh --skip-tests
   ```
   (`SILENCE_ALLOW_ROOT=1` only inside a single-user container.) If the venv already exists, delete `.venv` first. Without scikit-learn visible, the section says "Not available in this Python environment" with the reason, and nothing else changes. `SILENCE_ML_TRIAGE=0` switches the benchmark off.

What to compare with the Mac (arm64, little-endian), seed 42: `predictions_sha256 = 6949d3d6…` (per-episode predicted categories) and `results_sha256 = cbea084d…`. The Mac gave the same values with scikit-learn 1.5.1 / NumPy 1.26.4 and with 1.7.2 / 2.3.5. **It has not yet been run on s390x**: until you have run it there and seen the hashes, do not write that it reproduces on s390x. If they differ, report both library versions and the differing hash as a portability finding; do not edit the reference values.

## 8. Optional: start on boot with systemd

Needs `sudo`. Replace the placeholders and install the unit:

```bash
sed -e "s|__USER__|$(id -un)|g" -e "s|__APP_DIR__|$HOME/silence-engine-1.0.0|g" scripts/linuxone/silence-engine.service | sudo tee /etc/systemd/system/silence-engine.service
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now silence-engine
```

The unit runs as your user with `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only` and write access only to `data/`. Stop any `run.sh --background` instance first.

## s390x compatibility (checked against PyPI on 17 Oct 2026)

| Component | s390x status |
|---|---|
| CPython 3.10+ | Provided by RHEL, Ubuntu and SLES for s390x |
| `sqlite3`, `hashlib`, `json`, `math`, `random` | Python standard library (the detector needs nothing else) |
| Flask 3.1.3, Werkzeug 3.1.9, Jinja2 3.1.6, itsdangerous 2.2.0, click 8.5.0, blinker 1.9.0 | Pure-Python wheels (`py3-none-any`) |
| gunicorn 23.0.0, pytest 8.4.2 (+ pluggy, iniconfig, packaging, Pygments) | Pure-Python wheels |
| **MarkupSafe 3.0.4** | **No s390x wheel on PyPI** (wheels exist for x86_64, aarch64, ppc64le, armv7l, riscv64). pip builds it from the source distribution. With `gcc` + Python headers it compiles the optional C speed-ups; without them its `setup.py` retries the build without the extension, giving a fully working pure-Python install. |
| Frontend | Static files built on the Mac; no Node.js, npm or native modules on the server |
| **numpy, scikit-learn** *(optional ML benchmark only)* | **No s390x wheels on PyPI.** Use the LinuxONE ML container image's Python, which ships them. Not needed by the engine, API, dashboard or tests (ML tests skip cleanly without them). |
| Container images | Not used. If you containerise later, check that every base image has an `s390x` variant. |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Permission denied (publickey)` | `chmod 600` the key; confirm the user is `linux1` and the key matches the instance's key pair |
| `install.sh` says Python 3.10+ not found | Install one (table in step 3) and pass `PYTHON=python3.11` |
| `ensurepip`/`venv` missing | Install the distro's venv package (`python3-venv` on Ubuntu; included with `python3.11` on RHEL) |
| pip SSL or proxy errors | Check `curl https://pypi.org/simple/flask/`; set `HTTPS_PROXY` if your network requires it |
| MarkupSafe build warning about the C extension | Harmless — it falls back to pure Python. Install `gcc` and `python3.x-devel` to get the speed-ups |
| No internet on the VM | On the Mac: `.venv/bin/pip download -d wheelhouse -r requirements-lock.txt --platform any --only-binary=:all: --python-version 3.11` for the pure-Python wheels and `.venv/bin/pip download -d wheelhouse --no-deps --no-binary=:all: MarkupSafe==3.0.4` for the source; copy `wheelhouse/`; on the VM: `.venv/bin/pip install --no-index --find-links wheelhouse -r requirements-lock.txt` |
| `Address already in use` | Another process uses 8000: set `SILENCE_PORT=8010` in `.env` and adjust the tunnel |
| Dashboard says "Backend unreachable" through the tunnel | Is the server running (`curl` on the VM)? Is the tunnel command still open? |
| Write actions return 403 | You are not coming through the loopback interface (e.g. a reverse proxy). Set `SILENCE_API_TOKEN` in `.env`, restart, and enter the token on the Platform page |
| First page load shows "Preparing the synthetic demonstration" | The demo is being generated (~20 s on first start); the page refreshes itself |
| Tests fail on s390x | Keep the output — that is a genuine portability finding. Report it rather than skipping tests |

## Exposing the service

The default (loopback + SSH tunnel) is the recommended setup for a demo. Do **not** open port 8000 in the VM's firewall or security group. If a public URL is required, put a TLS-terminating reverse proxy (for example nginx) in front of `127.0.0.1:8000`, require authentication at the proxy, and set `SILENCE_API_TOKEN` (proxied requests look local to the app, so the loopback rule alone would not protect write endpoints). Nothing in this repository changes server firewall, SSH or system configuration.

## What this prototype demonstrates on IBM Z — and what it does not

**Demonstrates (once the steps above have been run):**

- The complete operational path — validation, feed-health analysis, detection, triage, evaluation and a tamper-evident audit trail — running on s390x Linux with only CPython, the standard library and pure-Python web packages.
- Optionally, an ML triage model (scikit-learn from the LinuxONE ML image) trained and benchmarked against the rules in the same notebook.
- Portability and determinism across architectures and endianness, checked by fingerprints rather than asserted.
- A deployment that respects least privilege: non-root user, loopback binding, SSH tunnel access, systemd hardening, no secrets in source.

**Does not demonstrate (future work):**

- No IBM-specific acceleration (for example on-chip AI inference, hardware crypto offload or z/OS integration) is used. The production engine is interpretable statistics in pure Python; the optional ML benchmark uses stock scikit-learn.
- The optional ML triage benchmark has been run only on macOS arm64. Run notebook Part C on the LinuxONE ML image to demonstrate it on s390x, and report what you see. On this synthetic data it found no detectable advantage over the rules, so it is a second opinion, not the production classifier.
- No Db2 integration. SQLite is used; `database.py` isolates all SQL so Db2 for Linux on Z (or Db2 for z/OS via a supported driver) could be added later and evaluated.
- No load testing, high-availability or multi-node setup.
- No real-world data; all results are synthetic.
