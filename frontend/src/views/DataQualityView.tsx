import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, ErrorState, FeedBadge, LoadingBlock, Notice } from "../components/Bits";
import { FLAG_LABELS, day, num, pct, range, service } from "../lib/format";
import { href, useApi, useWidth } from "../lib/hooks";
import type { DataQuality, StreamHealth } from "../lib/types";

const ISSUE_ORDER = [
  "missing_record",
  "missing_value",
  "zero_fill_suspected",
  "stale_feed",
  "flatline",
  "heartbeat_missing",
  "incomplete_batches",
  "malformed",
  "duplicate_conflict",
  "duplicate",
  "activity_on_closed_day",
  "late_arrival",
];

export function DataQualityView() {
  const dq = useApi<DataQuality>("/api/data-quality?days=90");
  const [type, setType] = useState("");
  const [stream, setStream] = useState("");
  const [limit, setLimit] = useState(40);

  const names = useMemo(() => {
    const m = new Map<string, StreamHealth>();
    dq.data?.streams.forEach((s) => m.set(s.stream_id, s));
    return m;
  }, [dq.data]);

  if (dq.error) return <ErrorState error={dq.error} onRetry={dq.reload} />;
  if (!dq.data) return <LoadingBlock rows={8} label="Loading data quality" />;
  const d = dq.data;
  const issues = d.issues.filter((i) => (!type || i.type === type) && (!stream || i.stream_id === stream));
  const totals = ISSUE_ORDER.filter((k) => d.issue_day_totals[k]).map((k) => ({ type: k, label: FLAG_LABELS[k] ?? k, days: d.issue_day_totals[k] }));
  const unhealthy = d.streams.filter((s) => s.status !== "HEALTHY");

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 32 }}>
      <div className="page-head">
        <div>
          <h1>Data quality</h1>
          <p className="lede">
            The health of the reporting pipeline, checked independently of activity levels: freshness, missing and malformed records,
            duplicates, flatlines, heartbeats and upload completeness. These findings are about the data, not about service delivery, and
            are kept apart from service-gap alerts on purpose.
          </p>
        </div>
      </div>

      {unhealthy.length > 0 ? (
        <Notice tone="warn">
          {unhealthy.length} feed{unhealthy.length === 1 ? " is" : "s are"} not healthy as of {day(d.as_of)}:{" "}
          {unhealthy.map((s) => `${s.site_name} ${service(s.service_type).toLowerCase()} (${s.status.toLowerCase()})`).join("; ")}. Activity on
          those streams cannot currently be measured reliably.
        </Notice>
      ) : (
        <Notice>All feeds were healthy over the last two weeks of data.</Notice>
      )}

      <section className="section" aria-labelledby="feeds-h">
        <div className="section-head">
          <h2 id="feeds-h">Feed health by service</h2>
          <p>Last 28 days unless stated</p>
        </div>
        <div className="table-wrap">
          <table className="data">
            <caption className="sr-only">Feed health by service</caption>
            <thead>
              <tr>
                <th>Service</th>
                <th>Status</th>
                <th className="r">Complete</th>
                <th className="r">Trusted</th>
                <th className="r">Last trusted count</th>
                <th className="r">Missing</th>
                <th className="r">Heartbeat gaps</th>
                <th className="r">Stale / flatline / zero-fill</th>
                <th className="r">Duplicates · malformed (all time)</th>
              </tr>
            </thead>
            <tbody>
              {d.streams.map((s) => {
                const r = s.counts_recent;
                const t = s.counts_total;
                return (
                  <tr key={s.stream_id}>
                    <td>
                      {s.site_name}
                      <span className="sub">{service(s.service_type)}</span>
                    </td>
                    <td>
                      <FeedBadge status={s.status} />
                    </td>
                    <td className="r num">{pct(s.completeness_recent)}</td>
                    <td className="r num">{pct(s.trusted_share_recent)}</td>
                    <td className="r num">
                      {s.last_trusted_date ? day(s.last_trusted_date) : "—"}
                      {s.days_since_trusted ? <span className="sub">{s.days_since_trusted} day{s.days_since_trusted === 1 ? "" : "s"} ago</span> : null}
                    </td>
                    <td className="r num">{(r.missing_record ?? 0) + (r.missing_value ?? 0)}</td>
                    <td className="r num">{r.heartbeat_missing ?? 0}</td>
                    <td className="r num">
                      {r.stale_feed ?? 0} / {r.flatline ?? 0} / {r.zero_fill_suspected ?? 0}
                    </td>
                    <td className="r num">
                      {s.duplicate_rows_total} · {t.malformed ?? 0}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="section" aria-labelledby="matrix-h">
        <div className="section-head">
          <div>
            <h2 id="matrix-h">Feed status, last 90 days</h2>
            <p>One cell per scheduled day. This shows whether data arrived and looked trustworthy — not whether activity was normal.</p>
          </div>
        </div>
        <div className="panel panel-pad" style={{ display: "grid", gap: 12 }}>
          <FeedMatrix d={d} names={names} />
          <div className="legend">
            <span className="item">
              <span className="sw" style={{ background: "color-mix(in oklab, var(--good) 30%, var(--surface-2))" }} />
              Record arrived and trusted
            </span>
            <span className="item">
              <span className="sw" style={{ background: "color-mix(in oklab, var(--warning) 45%, var(--surface-2))" }} />
              Minor warning (duplicate, late, malformed)
            </span>
            <span className="item">
              <span className="sw sw-suspect" />
              Count received but doubtful
            </span>
            <span className="item">
              <span className="sw sw-unknown" />
              No count received
            </span>
            <span className="item">
              <span className="sw sw-closed" />
              Closed by schedule
            </span>
          </div>
        </div>
      </section>

      <section className="section" aria-labelledby="types-h">
        <div className="section-head">
          <h2 id="types-h">Issue-days by type, all streams</h2>
          <p>Total stream-days carrying each flag over the full period</p>
        </div>
        <div className="panel panel-pad">
          <div style={{ width: "100%", height: Math.max(180, totals.length * 30 + 30) }} role="img" aria-label="Issue-days by type">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={totals} layout="vertical" margin={{ top: 4, right: 48, bottom: 4, left: 8 }} barCategoryGap={8}>
                <CartesianGrid horizontal={false} stroke="var(--grid)" />
                <XAxis type="number" tick={{ fontSize: 11, fill: "var(--muted)" }} axisLine={false} tickLine={false} allowDecimals={false} />
                <YAxis type="category" dataKey="label" width={190} tick={{ fontSize: 12, fill: "var(--ink-2)" }} axisLine={false} tickLine={false} />
                <Tooltip cursor={{ fill: "var(--ink)", fillOpacity: 0.05 }} content={<TotalsTip />} isAnimationActive={false} />
                <Bar dataKey="days" fill="var(--reporting)" radius={[0, 4, 4, 0]} maxBarSize={18} isAnimationActive={false}>
                  <LabelList dataKey="days" position="right" style={{ fontSize: 12, fill: "var(--ink-2)" }} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </section>

      <section className="section" aria-labelledby="log-h">
        <div className="section-head">
          <h2 id="log-h">Issue log</h2>
          <p>
            {issues.length} issue run{issues.length === 1 ? "" : "s"}
          </p>
        </div>
        <div className="toolbar">
          <label className="field">
            <span>Issue type</span>
            <select className="select" value={type} onChange={(e) => { setType(e.target.value); setLimit(40); }}>
              <option value="">All types</option>
              {ISSUE_ORDER.map((k) => (
                <option key={k} value={k}>
                  {FLAG_LABELS[k] ?? k}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Service</span>
            <select className="select" value={stream} onChange={(e) => { setStream(e.target.value); setLimit(40); }}>
              <option value="">All services</option>
              {d.streams.map((s) => (
                <option key={s.stream_id} value={s.stream_id}>
                  {s.site_name} — {service(s.service_type)}
                </option>
              ))}
            </select>
          </label>
        </div>
        {issues.length === 0 ? (
          <Empty title="No issues of this kind">The selected feed has no recorded issues of this type.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="data compact">
              <caption className="sr-only">Data-quality issue log</caption>
              <thead>
                <tr>
                  <th>Severity</th>
                  <th>Issue</th>
                  <th>Service</th>
                  <th>Dates</th>
                  <th className="r">Days</th>
                  <th>Investigate</th>
                </tr>
              </thead>
              <tbody>
                {issues.slice(0, limit).map((i) => {
                  const s = names.get(i.stream_id);
                  return (
                    <tr key={i.issue_id}>
                      <td>
                        <span className={`badge ${i.severity === "high" ? "st-critical" : i.severity === "medium" ? "st-warning" : "plain"}`}>{i.severity}</span>
                      </td>
                      <td>{i.label}</td>
                      <td>
                        {s?.site_name ?? i.site_id}
                        <span className="sub">{service(i.service_type)}</span>
                      </td>
                      <td>{range(i.start, i.end)}</td>
                      <td className="r num">{i.days}</td>
                      <td>
                        <a href={href(`/cases?site_id=${encodeURIComponent(i.site_id)}`)}>Cases at this site</a>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {issues.length > limit && (
          <div>
            <button className="btn" onClick={() => setLimit((l) => l + 60)}>
              Show more ({num(issues.length - limit)} remaining)
            </button>
          </div>
        )}
      </section>
      <p className="muted" style={{ fontSize: 12.5 }}>{d.note}</p>
    </div>
  );
}

function TotalsTip({ active, payload }: { active?: boolean; payload?: { payload: { label: string; days: number } }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="tip">
      <div className="t-head">{p.label}</div>
      <div>
        <b className="num">{num(p.days)}</b> stream-days flagged
      </div>
    </div>
  );
}

function FeedMatrix({ d, names }: { d: DataQuality; names: Map<string, StreamHealth> }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const labelW = 190;
  const n = d.feed_matrix.dates.length;
  const cw = n ? Math.max(3, (Math.max(width, 420) - labelW) / n) : 4;
  const ch = 12;
  const rowH = 18;
  const color: Record<string, string> = {
    ok: "color-mix(in oklab, var(--good) 30%, var(--surface-2))",
    warning: "color-mix(in oklab, var(--warning) 45%, var(--surface-2))",
    suspect: "url(#hatch-suspect)",
    missing: "url(#hatch-unknown)",
    closed: "var(--surface-2)",
  };
  return (
    <div ref={ref} style={{ overflowX: "auto" }}>
      <svg width={labelW + cw * n} height={d.feed_matrix.streams.length * rowH + 4} role="img" aria-label="Feed status matrix for the last 90 days">
        {d.feed_matrix.streams.map((s, si) => {
          const meta = names.get(s.stream_id);
          return (
            <g key={s.stream_id}>
              <text x={0} y={si * rowH + 10} style={{ fontSize: 11.5, fill: "var(--ink-2)" }}>
                {(meta?.site_name ?? s.stream_id).slice(0, 26)}
              </text>
              {s.cells.map((c, i) =>
                c ? (
                  <rect key={i} x={labelW + i * cw} y={si * rowH + (c === "closed" ? 4 : 0)} width={Math.max(1, cw - 1)} height={c === "closed" ? 4 : ch} rx={1} fill={color[c]}>
                    <title>{`${meta?.site_name ?? s.stream_id} · ${day(d.feed_matrix.dates[i])}: ${c}`}</title>
                  </rect>
                ) : null,
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
