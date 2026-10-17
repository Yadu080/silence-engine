import { useMemo, useState } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CATEGORY_META, FLAG_LABELS, STATUS_LABELS, compactNum, day, dayShort, num, pct, weekday } from "../lib/format";
import { navigate, useWidth } from "../lib/hooks";
import type { AggregateDay, Category, DayView } from "../lib/types";
import { Seg } from "./Bits";

export const Y_AXIS_WIDTH = 46;
export const CHART_MARGIN = { top: 18, right: 10, bottom: 0, left: 0 };

export interface CaseSpan {
  case_id: string;
  category: Category;
  onset: string;
  end: string;
}

interface Row {
  date: string;
  obs: number;
  short: number;
  unknown: number;
  expected: number | null;
  band: [number, number] | null;
  raw?: DayView;
  agg?: AggregateDay;
}

function toRows(days: DayView[]): Row[] {
  return days.map((d) => {
    const exp = d.status === "closed" || d.status === "closed_unusual" ? null : d.expected;
    const row: Row = {
      date: d.date,
      obs: 0,
      short: 0,
      unknown: 0,
      expected: exp,
      band: d.band_low !== null && d.band_high !== null && exp !== null ? [d.band_low, d.band_high] : null,
      raw: d,
    };
    if (d.status === "missing" || d.status === "suspect") {
      row.unknown = d.expected ?? d.typical_if_open ?? 0;
    } else {
      row.obs = d.observed ?? 0;
      if (d.status === "low" && d.expected !== null && d.observed !== null) row.short = Math.max(0, d.expected - d.observed);
    }
    return row;
  });
}

function aggRows(days: AggregateDay[]): Row[] {
  return days.map((d) => ({
    date: d.date,
    obs: d.observed,
    short: 0,
    unknown: d.unknown,
    expected: d.expected || null,
    band: null,
    agg: d,
  }));
}

function Tip({ active, payload }: { active?: boolean; payload?: { payload: Row }[] }) {
  if (!active || !payload || !payload.length) return null;
  const r = payload[0].payload;
  if (r.agg) {
    const a = r.agg;
    return (
      <div className="tip">
        <div className="t-head">
          {weekday(r.date)} {day(r.date)}
        </div>
        <div className="t-row">
          <span className="k box sw-observed" />
          <span>Observed (trusted)</span>
          <b>{num(a.observed)}</b>
        </div>
        <div className="t-row">
          <span className="k box sw-unknown" />
          <span>Expected, but no trustworthy data</span>
          <b>{num(a.unknown)}</b>
        </div>
        <div className="t-row">
          <span className="k ln-expected" />
          <span>Expected</span>
          <b>{num(a.expected)}</b>
        </div>
        <div className="t-note">
          {a.streams_reporting} services reporting, {a.streams_unknown} unknown
          {a.case_streams ? `, ${a.case_streams} inside an investigation window` : ""}.
        </div>
      </div>
    );
  }
  const d = r.raw!;
  return (
    <div className="tip">
      <div className="t-head">
        {weekday(d.date)} {day(d.date)}
      </div>
      <div className="t-row">
        <span className="k box sw-observed" />
        <span>Observed</span>
        <b>{d.observed === null ? "not reported" : num(d.observed)}</b>
      </div>
      <div className="t-row">
        <span className="k ln-expected" />
        <span>Expected</span>
        <b>{d.expected === null ? "—" : num(d.expected)}</b>
      </div>
      {d.band_low !== null && d.band_high !== null && d.expected !== null && (
        <div className="t-row">
          <span className="k" />
          <span>Usual range (±2σ)</span>
          <b>
            {num(d.band_low)}–{num(d.band_high)}
          </b>
        </div>
      )}
      {d.status === "low" && (
        <div className="t-row">
          <span className="k box sw-shortfall" />
          <span>Shortfall</span>
          <b>{pct(d.shortfall_frac)}</b>
        </div>
      )}
      {d.z !== null && d.status !== "closed" && (
        <div className="t-row">
          <span className="k" />
          <span>Robust deviation (z)</span>
          <b>{d.z.toFixed(1)}</b>
        </div>
      )}
      <div className="t-note">
        {STATUS_LABELS[d.status] ?? d.status}
        {d.schedule_inferred && d.status === "missing" ? " · schedule inferred" : ""}
        {d.flags.length > 0 && (
          <>
            <br />
            {d.flags.map((f) => FLAG_LABELS[f] ?? f).join(" · ")}
          </>
        )}
        {d.case_id && (
          <>
            <br />
            Case <span className="mono">{d.case_id}</span> — click to open
          </>
        )}
      </div>
    </div>
  );
}

export function SilenceChart({
  days,
  aggregate,
  cases = [],
  focusCase,
  height = 300,
  showTableToggle = true,
  caption,
}: {
  days?: DayView[];
  aggregate?: AggregateDay[];
  cases?: CaseSpan[];
  focusCase?: CaseSpan;
  height?: number;
  showTableToggle?: boolean;
  caption: string;
}) {
  const [mode, setMode] = useState<"chart" | "table">("chart");
  const rows = useMemo(() => (aggregate ? aggRows(aggregate) : toRows(days ?? [])), [days, aggregate]);
  const first = rows[0]?.date;
  const last = rows[rows.length - 1]?.date;
  const spans = cases
    .filter((c) => first && last && c.end >= first && c.onset <= last)
    .map((c) => ({ ...c, x1: c.onset < first! ? first! : c.onset, x2: c.end > last! ? last! : c.end }));
  const hasShort = rows.some((r) => r.short > 0);
  const hasUnknown = rows.some((r) => r.unknown > 0);
  const [wrapRef, wrapW] = useWidth<HTMLDivElement>();
  const plotW = Math.max(0, wrapW - Y_AXIS_WIDTH - CHART_MARGIN.left - CHART_MARGIN.right);
  const slot = rows.length ? plotW / rows.length : 0;
  const tickW = Math.max(3, Math.min(28, slot * 0.9));

  return (
    <figure style={{ margin: 0, display: "grid", gap: 10 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
        <div className="legend" aria-label="Legend">
          <span className="item">
            <span className="sw sw-observed" />
            {aggregate ? "Observed (trusted counts)" : "Observed"}
          </span>
          <span className="item">
            <span className="ln ln-expected" />
            Expected for that day (median of prior same weekdays)
          </span>
          {hasShort && (
            <span className="item">
              <span className="sw sw-shortfall" />
              Missing activity on flagged days
            </span>
          )}
          {hasUnknown && (
            <span className="item">
              <span className="sw sw-unknown" />
              No trustworthy data (unknown, not zero)
            </span>
          )}
          {spans.length > 0 && !focusCase && (
            <span className="item">
              <span className="sw sw-case" style={{ ["--c" as string]: "var(--ink-2)" }} />
              Investigation window
            </span>
          )}
        </div>
        {showTableToggle && (
          <Seg
            label="Display as"
            value={mode}
            onChange={setMode}
            options={[
              { value: "chart", label: "Chart" },
              { value: "table", label: "Table" },
            ]}
          />
        )}
      </div>
      {mode === "chart" ? (
        <div ref={wrapRef} style={{ width: "100%", height }} role="img" aria-label={caption}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={CHART_MARGIN} barCategoryGap={rows.length > 120 ? 0 : "18%"}>
              <CartesianGrid vertical={false} stroke="var(--grid)" />
              <XAxis
                dataKey="date"
                tickFormatter={dayShort}
                tick={{ fontSize: 11, fill: "var(--muted)" }}
                tickLine={false}
                axisLine={{ stroke: "var(--axis)" }}
                minTickGap={28}
                interval="preserveStartEnd"
              />
              <YAxis
                width={Y_AXIS_WIDTH}
                tickFormatter={(v: number) => compactNum(v)}
                tick={{ fontSize: 11, fill: "var(--muted)" }}
                tickLine={false}
                axisLine={false}
                allowDecimals={false}
              />
              {spans.map((s) => (
                <ReferenceArea
                  key={s.case_id}
                  x1={s.x1}
                  x2={s.x2}
                  fill={`var(--${CATEGORY_META[s.category].css})`}
                  fillOpacity={focusCase ? 0.08 : 0.11}
                  stroke="none"
                  ifOverflow="hidden"
                  style={{ cursor: focusCase ? "default" : "pointer" }}
                  onClick={() => !focusCase && navigate(`/cases/${encodeURIComponent(s.case_id)}`)}
                />
              ))}
              <Bar dataKey="obs" stackId="a" fill="var(--observed)" fillOpacity={0.82} maxBarSize={24} isAnimationActive={false} onClick={(d: { payload?: Row }) => openCase(d?.payload)} style={{ cursor: "pointer" }} />
              <Bar dataKey="short" stackId="a" fill="var(--access)" fillOpacity={0.72} maxBarSize={24} isAnimationActive={false} onClick={(d: { payload?: Row }) => openCase(d?.payload)} />
              <Bar dataKey="unknown" stackId="a" fill="url(#hatch-unknown)" maxBarSize={24} radius={[3, 3, 0, 0]} isAnimationActive={false} onClick={(d: { payload?: Row }) => openCase(d?.payload)} />
              <Line
                dataKey="expected"
                stroke="none"
                activeDot={false}
                isAnimationActive={false}
                dot={(p: { cx?: number; cy?: number; value?: number | null; index?: number }) =>
                  p.cx === undefined || p.cy === undefined || p.value === null || p.value === undefined ? (
                    <g key={`e-${p.index}`} />
                  ) : (
                    <line
                      key={`e-${p.index}`}
                      x1={p.cx - tickW / 2}
                      x2={p.cx + tickW / 2}
                      y1={p.cy}
                      y2={p.cy}
                      stroke="var(--expected-line)"
                      strokeWidth={2.5}
                      strokeLinecap="round"
                    />
                  )
                }
              />
              {focusCase && first && focusCase.onset >= first && (
                <ReferenceLine
                  x={focusCase.onset}
                  stroke="var(--ink)"
                  strokeWidth={1}
                  label={{ value: "Decline began", position: "insideTopLeft", fill: "var(--ink)", fontSize: 11, dy: -16 }}
                />
              )}
              <Tooltip content={<Tip />} cursor={{ fill: "var(--ink)", fillOpacity: 0.06 }} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="table-wrap" style={{ maxHeight: height + 60, overflowY: "auto" }}>
          <table className="data compact">
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr>
                <th>Date</th>
                <th>Status</th>
                <th className="r">Observed</th>
                <th className="r">Expected</th>
                <th className="r">{aggregate ? "Unknown" : "Shortfall"}</th>
              </tr>
            </thead>
            <tbody>
              {rows
                .slice()
                .reverse()
                .map((r) => (
                  <tr key={r.date}>
                    <td>
                      {weekday(r.date)} {day(r.date)}
                    </td>
                    <td>{r.raw ? STATUS_LABELS[r.raw.status] : `${r.agg!.streams_unknown} unknown`}</td>
                    <td className="r num">{r.raw ? (r.raw.observed === null ? "not reported" : num(r.raw.observed)) : num(r.obs)}</td>
                    <td className="r num">{r.expected === null ? "—" : num(r.expected)}</td>
                    <td className="r num">{r.raw ? (r.raw.status === "low" ? pct(r.raw.shortfall_frac) : "—") : num(r.unknown)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
      <figcaption className="sr-only">{caption}</figcaption>
    </figure>
  );
}

function openCase(row?: Row) {
  const id = row?.raw?.case_id;
  if (id) navigate(`/cases/${encodeURIComponent(id)}`);
}

/** Global SVG pattern definitions referenced by charts (rendered once). */
export function SvgDefs() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true" focusable="false">
      <defs>
        <pattern id="hatch-unknown" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="5" height="5" fill="var(--reporting)" fillOpacity="0.12" />
          <line x1="0" y1="0" x2="0" y2="5" stroke="var(--reporting)" strokeWidth="2" strokeOpacity="0.85" />
        </pattern>
        <pattern id="hatch-suspect" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="6" height="6" fill="var(--reporting)" fillOpacity="0.16" />
          <line x1="0" y1="0" x2="0" y2="6" stroke="var(--reporting)" strokeWidth="1.6" strokeOpacity="0.75" />
        </pattern>
      </defs>
    </svg>
  );
}

/** Feed-health and context strips aligned to the chart's day columns. */
export function DayStrips({ days }: { days: DayView[] }) {
  const n = days.length;
  const cols = { gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` };
  const pad = { paddingLeft: Y_AXIS_WIDTH + CHART_MARGIN.left, paddingRight: CHART_MARGIN.right };
  const feedClass = (d: DayView) => {
    if (!d.scheduled_open) return "none";
    if (d.status === "missing") return "bad";
    if (d.status === "suspect") return "warn";
    return "ok";
  };
  const rows: { label: string; cls: (d: DayView) => string; title: (d: DayView) => string }[] = [
    {
      label: "Feed health",
      cls: feedClass,
      title: (d) =>
        !d.scheduled_open
          ? "Closed"
          : d.status === "missing"
            ? "No count received"
            : d.status === "suspect"
              ? `Doubtful: ${d.flags.map((f) => FLAG_LABELS[f] ?? f).join(", ")}`
              : "Record, heartbeat and batches OK",
    },
    {
      label: "Weather severity",
      cls: (d) => (d.weather ? `ctx${Math.min(3, d.weather)}` : "none"),
      title: (d) => `Weather index ${d.weather ?? "n/a"}`,
    },
    { label: "Transport disruption", cls: (d) => (d.transport ? "ctx3" : "none"), title: (d) => (d.transport ? "Transport disruption reported" : "None") },
    { label: "Reported outage", cls: (d) => (d.outage ? "ctx3" : "none"), title: (d) => (d.outage ? "Operational outage reported" : "None") },
  ];
  return (
    <div className="strip-rows" aria-label="Feed health and context by day">
      {rows.map((r) => (
        <div key={r.label} style={{ display: "grid", gap: 2 }}>
          <div style={{ ...pad, fontSize: 11.5, color: "var(--muted)" }}>{r.label}</div>
          <div style={pad}>
            <div className="strip-cells" style={cols}>
              {days.map((d) => (
                <span key={d.date} className={r.cls(d)} title={`${day(d.date)}: ${r.title(d)}`} />
              ))}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
