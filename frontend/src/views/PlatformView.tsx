import { useRef, useState } from "react";
import { ErrorState, LoadingBlock, Notice } from "../components/Bits";
import { IconCheck, IconDownload, IconX } from "../components/Icons";
import { ApiError, api, apiUrl, getToken, requestHealthRefresh, setToken } from "../lib/api";
import { dateTime, num, shortHash } from "../lib/format";
import { useApi } from "../lib/hooks";
import type { AuditEntry, Health, ImportResult, SchemaField, SystemInfo } from "../lib/types";

export function PlatformView({ health }: { health: Health }) {
  const info = useApi<SystemInfo>("/api/system-info");
  if (info.error) return <ErrorState error={info.error} onRetry={info.reload} />;
  if (!info.data) return <LoadingBlock rows={10} label="Loading platform information" />;
  const s = info.data;
  const rt = s.runtime;

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 40 }}>
      <div className="page-head">
        <div>
          <h1>Platform</h1>
          <p className="lede">
            Facts reported by the running backend: where the detection pipeline and audit database actually execute. Nothing on this page is
            hard-coded; the architecture line comes from the Python interpreter and, on IBM Z, from the kernel’s <code>/proc/sysinfo</code>.
          </p>
        </div>
      </div>

      <section className="hero" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.2fr)" }} aria-labelledby="arch-h">
        <div>
          <h2 id="arch-h" className="muted" style={{ fontSize: 13, fontWeight: 500 }}>
            Processor architecture
          </h2>
          <div className="arch-big num">{rt.machine}</div>
          <div className="verdict-line">
            {rt.is_s390x ? <IconCheck size={16} /> : <IconX size={16} />}
            <span>{rt.architecture_verdict}</span>
          </div>
          {rt.ibm_z_sysinfo && (
            <dl className="kv" style={{ marginTop: 14 }}>
              {Object.entries(rt.ibm_z_sysinfo).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
          )}
          {rt.deployment_label && (
            <p className="muted" style={{ marginTop: 12, fontSize: 13 }}>
              Deployment label: <b className="ink2">{rt.deployment_label}</b> — {rt.deployment_label_note}
            </p>
          )}
        </div>
        <dl className="kv">
          <dt>Operating system</dt>
          <dd>
            {rt.os_pretty_name ?? rt.system} <span className="muted">({rt.system} {rt.release})</span>
          </dd>
          <dt>Python</dt>
          <dd>
            {rt.python_implementation} {rt.python_version} <span className="muted">· {rt.python_compiler}</span>
          </dd>
          <dt>Byte order</dt>
          <dd>{rt.byteorder}-endian</dd>
          <dt>CPUs visible</dt>
          <dd>{rt.cpu_count ?? "—"}</dd>
          <dt>Host</dt>
          <dd>{rt.hostname}</dd>
          <dt>Web stack</dt>
          <dd>
            Flask {rt.flask_version ?? "—"} · Werkzeug {rt.werkzeug_version ?? "—"}
            {rt.gunicorn_version ? ` · gunicorn ${rt.gunicorn_version}` : ""}
          </dd>
          <dt>Optional ML stack</dt>
          <dd>
            {rt.ml_packages && Object.values(rt.ml_packages).some(Boolean)
              ? Object.entries(rt.ml_packages)
                  .filter(([, v]) => v)
                  .map(([k, v]) => `${k} ${v}`)
                  .join(" · ")
              : "not installed in this Python (optional; the ML benchmark is then marked unavailable)"}
          </dd>
          <dt>Database</dt>
          <dd>
            {s.database.engine} {s.database.sqlite_version} · {s.database.journal_mode.toUpperCase()} · integrity {s.database.integrity}
          </dd>
          <dt>Backend health</dt>
          <dd>
            {health.status} · process {rt.pid} up {Math.round(rt.uptime_seconds / 60)} min
          </dd>
        </dl>
      </section>

      <section className="section" aria-labelledby="diag-h">
        <div className="section-head">
          <h2 id="diag-h">Where each component runs</h2>
          <p>The same Python backend runs unchanged on macOS (arm64/x86_64) and LinuxONE (s390x)</p>
        </div>
        <Diagram machine={rt.machine} />
      </section>

      <div className="grid-auto">
        <section className="section" aria-labelledby="data-h">
          <h2 id="data-h">Loaded data</h2>
          <dl className="kv">
            <dt>Mode</dt>
            <dd>{s.data.mode === "synthetic" ? "Synthetic demonstration" : s.data.mode === "mixed" ? "Synthetic + imported" : s.data.mode ?? "None"}</dd>
            <dt>Seed</dt>
            <dd>{s.data.demo_seed ?? "—"}</dd>
            <dt>Records</dt>
            <dd>
              {num(s.data.observations.records)} · {s.data.observations.sites} sites · {s.data.observations.streams} streams
            </dd>
            <dt>Period</dt>
            <dd>
              {s.data.observations.first_date} → {s.data.observations.last_date}
            </dd>
            <dt>Sources</dt>
            <dd>
              {Object.entries(s.data.observations.by_source)
                .map(([k, v]) => `${k}: ${num(v)}`)
                .join(" · ")}
            </dd>
            <dt>Dataset fingerprint</dt>
            <dd className="hash">{s.data.dataset_fingerprint}</dd>
            <dt>Ground-truth labels</dt>
            <dd>{s.data.labels_file_present ? "Present in a separate file (evaluation only)" : "Not present"}</dd>
          </dl>
          <a className="btn small" href={apiUrl("api/export/observations.csv")} style={{ width: "fit-content" }}>
            <IconDownload size={14} /> Export loaded observations (CSV)
          </a>
        </section>
        <section className="section" aria-labelledby="run-h">
          <h2 id="run-h">Analysis runs</h2>
          {s.analysis.current && (
            <dl className="kv">
              <dt>Current run</dt>
              <dd className="mono">{s.analysis.current.run_id}</dd>
              <dt>Finished</dt>
              <dd>
                {dateTime(s.analysis.current.finished_at)} in {s.analysis.current.duration_ms} ms
              </dd>
              <dt>Executed on</dt>
              <dd>
                {s.analysis.current.platform.machine} · {s.analysis.current.platform.system} · Python {s.analysis.current.platform.python}
              </dd>
              <dt>Cases produced</dt>
              <dd>{s.analysis.current.n_cases}</dd>
              <dt>Thresholds</dt>
              <dd>
                <span className="mono" style={{ fontSize: 12 }}>
                  z={String(s.detection_params.values.z_threshold)} shortfall={String(s.detection_params.values.min_shortfall)} persistence=
                  {String(s.detection_params.values.min_persistence)}
                </span>
                <span className="sub muted" style={{ display: "block", fontSize: 12 }}>
                  {s.detection_params.source}
                </span>
              </dd>
            </dl>
          )}
          <div className="table-wrap">
            <table className="data compact">
              <thead>
                <tr>
                  <th>Finished</th>
                  <th>Machine</th>
                  <th className="r">Records</th>
                  <th className="r">Cases</th>
                  <th className="r">ms</th>
                </tr>
              </thead>
              <tbody>
                {s.analysis.history.map((h) => (
                  <tr key={h.run_id}>
                    <td>{dateTime(h.finished_at)}</td>
                    <td>
                      {h.machine} · {h.system}
                    </td>
                    <td className="r num">{num(h.n_observations)}</td>
                    <td className="r num">{h.n_cases}</td>
                    <td className="r num">{h.duration_ms}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <Manage info={s} health={health} onChanged={info.reload} />
      <AuditSection valid={s.audit.valid} records={s.audit.records} head={s.audit.head_hash ?? null} />
    </div>
  );
}

function Diagram({ machine }: { machine: string }) {
  const here = machine === "s390x" ? "linuxone" : "local";
  const box = (x: number, y: number, w: number, h: number, title: string, lines: string[], active: boolean) => (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={10} fill="var(--surface)" stroke={active ? "var(--ink)" : "var(--line-strong)"} strokeWidth={active ? 2 : 1} />
      <text x={x + 16} y={y + 26} style={{ fontSize: 14, fontWeight: 600, fill: "var(--ink)" }}>
        {title}
      </text>
      {lines.map((l, i) => (
        <text key={l} x={x + 16} y={y + 50 + i * 19} style={{ fontSize: 12.5, fill: "var(--ink-2)" }}>
          {l}
        </text>
      ))}
    </g>
  );
  return (
    <div className="panel panel-pad diagram">
      <svg viewBox="0 0 980 300" role="img" aria-label="Architecture: browser, development Mac, and LinuxONE server">
        <defs>
          <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0 10 5 0 10z" fill="var(--ink-2)" />
          </marker>
        </defs>
        {box(20, 30, 250, 112, "Analyst’s browser", ["React dashboard (static files)", "Talks only to /api on one origin", "Via SSH tunnel or HTTPS proxy"], false)}
        {box(20, 170, 250, 112, "Development Mac", ["Node + Vite build → frontend/dist", "Same backend + tests run locally", here === "local" ? "← this backend is running here" : "Packages a release tarball"], here === "local")}
        {box(360, 30, 600, 252, "LinuxONE (s390x) — operational backend", [], here === "linuxone")}
        {box(384, 76, 270, 186, "gunicorn + Flask (non-root)", ["Ingestion & CSV validation", "Feed-health checks", "Baselines, CUSUM, episodes", "Triage & evidence", "Evaluation harness", "REST API + static dashboard"], false)}
        {box(674, 76, 266, 92, "SQLite (WAL)", ["Observations, cases, reviews", "Hash-chained audit log"], false)}
        {box(674, 178, 266, 84, "Separate label file", ["Synthetic ground truth", "Read by evaluation only"], false)}
        <path d="M270 86 H 384" stroke="var(--ink-2)" strokeWidth={1.5} markerEnd="url(#arr)" fill="none" />
        <text x={286} y={78} style={{ fontSize: 11.5, fill: "var(--muted)" }}>
          HTTPS / tunnel
        </text>
        <path d="M270 226 C 320 226, 330 200, 384 200" stroke="var(--ink-2)" strokeWidth={1.5} strokeDasharray="4 4" markerEnd="url(#arr)" fill="none" />
        <text x={280} y={252} style={{ fontSize: 11.5, fill: "var(--muted)" }}>
          scp release (no Node needed on server)
        </text>
        <path d="M654 122 H 674" stroke="var(--ink-2)" strokeWidth={1.5} markerEnd="url(#arr)" fill="none" />
        <path d="M654 220 H 674" stroke="var(--ink-2)" strokeWidth={1.5} markerEnd="url(#arr)" fill="none" />
      </svg>
      <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
        Highlighted box: where this backend is running right now (from <code>platform.machine()</code> = {machine}). Db2 for z/OS or Db2 on
        Linux could replace SQLite behind the database module; that is future work and is not configured here.
      </p>
    </div>
  );
}

function Manage({ info, health, onChanged }: { info: SystemInfo; health: Health; onChanged: () => void }) {
  const schema = useApi<{ fields: SchemaField[]; notes: string[] }>("/api/schema");
  const [seed, setSeed] = useState<string>(info.data.demo_seed ?? "42");
  const [genMsg, setGenMsg] = useState<string | null>(null);
  const [genErr, setGenErr] = useState<string | null>(null);
  const [mode, setMode] = useState<"append" | "replace">("append");
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [impErr, setImpErr] = useState<{ msg: string; details?: unknown } | null>(null);
  const [token, setTok] = useState(getToken());
  const file = useRef<HTMLInputElement>(null);
  const running = health.job?.status === "running";
  const canWrite = info.write_access.client_is_loopback || info.write_access.token_required;

  async function regenerate(e: React.FormEvent) {
    e.preventDefault();
    setGenErr(null);
    setGenMsg(null);
    const n = Number(seed);
    if (!Number.isInteger(n) || n < 0) {
      setGenErr("Seed must be a whole number of zero or more.");
      return;
    }
    try {
      await api.post("/api/generate-demo-data", { seed: n });
      requestHealthRefresh();
      setGenMsg("Regenerating — the dashboard will refresh when the job finishes (about 20 seconds).");
    } catch (err) {
      setGenErr(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    const f = file.current?.files?.[0];
    if (!f) {
      setImpErr({ msg: "Choose a CSV file first." });
      return;
    }
    if (mode === "replace" && !window.confirm("Replace ALL loaded data (including the synthetic demo) with this file?")) return;
    const form = new FormData();
    form.append("file", f);
    form.append("mode", mode);
    setImporting(true);
    setImpErr(null);
    setResult(null);
    try {
      const r = await api.upload<ImportResult>("/api/import-csv", form);
      setResult(r);
      onChanged();
      requestHealthRefresh();
    } catch (err) {
      if (err instanceof ApiError) setImpErr({ msg: err.message, details: err.details });
      else setImpErr({ msg: String(err) });
    } finally {
      setImporting(false);
    }
  }

  const validation = result?.validation ?? ((impErr?.details as ImportResult["validation"]) || null);

  return (
    <section className="section" aria-labelledby="manage-h">
      <h2 id="manage-h">Data management</h2>
      {!canWrite && (
        <Notice tone="warn">
          This browser is not connected from the server itself and no API token is configured, so write actions are disabled by the server.
          Use an SSH tunnel, or set <code>SILENCE_API_TOKEN</code> on the server.
        </Notice>
      )}
      <div className="grid-auto">
        <form className="panel panel-pad" style={{ display: "grid", gap: 12, alignContent: "start" }} onSubmit={regenerate}>
          <h3>Regenerate the synthetic demo</h3>
          <p className="muted" style={{ fontSize: 13 }}>
            Replaces all loaded data with a fresh synthetic dataset, re-runs the evaluation (thresholds re-tuned on its training period) and
            the analysis. The same seed always produces the same data.
          </p>
          <label className="field" style={{ maxWidth: 180 }}>
            <span>Seed</span>
            <input className="input num" inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value)} />
          </label>
          {genErr && <Notice tone="error">{genErr}</Notice>}
          {genMsg && <p className="muted" role="status">{genMsg}</p>}
          <div>
            <button className="btn primary" type="submit" disabled={running}>
              {running ? "A job is running…" : "Regenerate demo data"}
            </button>
          </div>
        </form>

        <form className="panel panel-pad" style={{ display: "grid", gap: 12, alignContent: "start" }} onSubmit={upload}>
          <h3>Import a CSV</h3>
          <p className="muted" style={{ fontSize: 13 }}>
            Aggregate daily records only. Rows are validated before analysis; blank counts are stored as missing, never as zero.
          </p>
          <div className="dropzone">
            <input ref={file} type="file" accept=".csv,text/csv" aria-label="CSV file" />
            <div className="seg" role="group" aria-label="Import mode">
              <button type="button" aria-pressed={mode === "append"} onClick={() => setMode("append")}>
                Append
              </button>
              <button type="button" aria-pressed={mode === "replace"} onClick={() => setMode("replace")}>
                Replace all
              </button>
            </div>
          </div>
          {impErr && <Notice tone="error">{impErr.msg}</Notice>}
          {validation && (
            <div style={{ fontSize: 13, display: "grid", gap: 6 }}>
              <p>
                {num(validation.rows_seen)} rows read · <b>{num(validation.rows_accepted)} accepted</b> · {num(validation.rows_rejected)} rejected ·{" "}
                {validation.missing_counts} missing counts · {validation.malformed_counts} malformed counts
              </p>
              {validation.issues.length > 0 && (
                <ul className="limits" style={{ maxHeight: 160, overflowY: "auto" }}>
                  {validation.issues.map((i, k) => (
                    <li key={k}>
                      {i.row ? `Row ${i.row}` : "Header"} · {i.field}: {i.message} <span className="muted">({i.severity})</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {result && (
            <p className="muted" role="status">
              Imported as batch <span className="mono">{result.batch_id}</span>; data mode is now {result.data_mode}. Analysis re-ran
              {result.analysis ? ` (${result.analysis.cases} cases)` : ""}.
            </p>
          )}
          <div>
            <button className="btn primary" type="submit" disabled={importing || running}>
              {importing ? "Validating and importing…" : "Validate and import"}
            </button>
          </div>
          <details>
            <summary className="muted" style={{ cursor: "pointer", fontSize: 13 }}>
              CSV schema
            </summary>
            {schema.data && (
              <div className="table-wrap" style={{ marginTop: 8 }}>
                <table className="data compact">
                  <thead>
                    <tr>
                      <th>Column</th>
                      <th>Type</th>
                      <th>Meaning</th>
                    </tr>
                  </thead>
                  <tbody>
                    {schema.data.fields.map((f) => (
                      <tr key={f.name}>
                        <td className="mono">
                          {f.name}
                          {f.required ? " *" : ""}
                        </td>
                        <td>{f.type}</td>
                        <td>{f.description}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </details>
        </form>

        <div className="panel panel-pad" style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <h3>Write access</h3>
          <p className="muted" style={{ fontSize: 13 }}>
            {info.write_access.token_required
              ? "This server requires an API token for write actions. It is kept for this browser tab only."
              : info.write_access.client_is_loopback
                ? "No token is configured; writes are accepted because this browser reaches the server from the same machine (or through an SSH tunnel)."
                : "No token is configured and you are not on the server, so writes are refused."}
          </p>
          {info.write_access.token_required && (
            <form
              style={{ display: "flex", gap: 8 }}
              onSubmit={(e) => {
                e.preventDefault();
                setToken(token);
              }}
            >
              <input className="input" type="password" autoComplete="off" value={token} onChange={(e) => setTok(e.target.value)} placeholder="API token" aria-label="API token" style={{ flex: 1 }} />
              <button className="btn" type="submit">
                Use token
              </button>
            </form>
          )}
        </div>
      </div>
    </section>
  );
}

function AuditSection({ valid, records, head }: { valid: boolean; records: number; head: string | null }) {
  const audit = useApi<{ total: number; entries: AuditEntry[] }>("/api/audit?limit=25");
  return (
    <section className="section" aria-labelledby="aud-h">
      <div className="section-head">
        <div>
          <h2 id="aud-h">Audit trail</h2>
          <p>Every state change appends a record whose SHA-256 hash covers the previous one. Editing or deleting a row breaks the chain.</p>
        </div>
        <span className={`badge ${valid ? "st-good" : "st-critical"}`}>
          {valid ? <IconCheck size={12} className="ico" /> : <IconX size={12} className="ico" />}
          Chain {valid ? "verified" : "BROKEN"} · {records} records
        </span>
      </div>
      {head && <p className="hash">Head {shortHash(head, 24)}</p>}
      {audit.error ? (
        <ErrorState error={audit.error} onRetry={audit.reload} />
      ) : !audit.data ? (
        <LoadingBlock rows={4} />
      ) : (
        <div className="table-wrap">
          <table className="data compact">
            <thead>
              <tr>
                <th>When</th>
                <th>Action</th>
                <th>Entity</th>
                <th>Actor</th>
                <th>Details</th>
                <th>Hash</th>
              </tr>
            </thead>
            <tbody>
              {audit.data.entries.map((a) => (
                <tr key={a.id}>
                  <td>{dateTime(a.ts)}</td>
                  <td>{a.action.replace(/_/g, " ")}</td>
                  <td className="mono">{a.entity_id ?? "—"}</td>
                  <td>{a.actor}</td>
                  <td style={{ maxWidth: 380 }}>
                    <span className="muted" style={{ fontSize: 12 }}>
                      {summarise(a.details)}
                    </span>
                  </td>
                  <td className="hash">{a.hash.slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function summarise(d: Record<string, unknown>): string {
  return Object.entries(d)
    .filter(([, v]) => typeof v !== "object" || v === null)
    .slice(0, 5)
    .map(([k, v]) => `${k}: ${String(v)}`)
    .join(" · ");
}
