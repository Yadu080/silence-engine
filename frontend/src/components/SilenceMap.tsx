import { useMemo, useState } from "react";
import { CATEGORY_META, STATUS_LABELS, day, pct, service, weekday } from "../lib/format";
import { href, navigate, useWidth } from "../lib/hooks";
import type { DayStatus, SilenceMapData } from "../lib/types";

const CODE_TO_STATUS: DayStatus[] = ["closed", "closed_unusual", "ok", "low", "missing", "suspect", "no_baseline"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

interface Hover {
  x: number;
  y: number;
  stream: number;
  day: number;
}

export function SilenceMap({ data, compact = false }: { data: SilenceMapData; compact?: boolean }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<Hover | null>(null);
  const labelW = compact ? 196 : 214;
  const n = data.dates.length;
  const cellW = n ? Math.max(2, (Math.max(width, 480) - labelW - 4) / n) : 4;
  const cellH = compact ? 13 : 16;
  const rowH = cellH + (compact ? 14 : 12);
  const top = 22;
  const svgW = labelW + cellW * n + 4;
  const svgH = top + rowH * data.streams.length + 4;

  const months = useMemo(() => {
    const out: { i: number; label: string }[] = [];
    data.dates.forEach((d, i) => {
      if (i === 0 || d.slice(8, 10) === "01") {
        const m = Number(d.slice(5, 7));
        out.push({ i, label: m === 1 || i === 0 ? `${MONTHS[m - 1]} ${d.slice(0, 4)}` : MONTHS[m - 1] });
      }
    });
    return out.filter((m, k) => k === 0 || m.i - out[k - 1].i > (compact ? 6 : 4));
  }, [data.dates, compact]);

  const runs = useMemo(
    () =>
      data.streams.map((s) => {
        const out: { ci: number; from: number; to: number }[] = [];
        let cur: { ci: number; from: number; to: number } | null = null;
        s.cells.forEach((c, i) => {
          const ci = c ? c[2] : -1;
          if (ci >= 0 && cur && cur.ci === ci) cur.to = i;
          else {
            if (cur) out.push(cur);
            cur = ci >= 0 ? { ci, from: i, to: i } : null;
          }
        });
        if (cur) out.push(cur);
        return out;
      }),
    [data.streams],
  );

  function fill(code: number, gap: number | null): { fill: string; opacity: number; h: number } {
    switch (CODE_TO_STATUS[code]) {
      case "closed":
        return { fill: "var(--surface-3)", opacity: 0.55, h: 0.36 };
      case "closed_unusual":
        return { fill: "var(--expected)", opacity: 0.5, h: 1 };
      case "low":
        return { fill: "var(--access)", opacity: 0.32 + 0.68 * Math.min(1, gap ?? 0.5), h: 1 };
      case "missing":
        return { fill: "url(#hatch-unknown)", opacity: 1, h: 1 };
      case "suspect":
        return { fill: "url(#hatch-suspect)", opacity: 1, h: 1 };
      case "no_baseline":
        return { fill: "var(--surface-2)", opacity: 1, h: 1 };
      default:
        return { fill: "var(--surface-3)", opacity: 1, h: 1 };
    }
  }

  const hovered = hover ? data.streams[hover.stream] : null;
  const hoveredCell = hover && hovered ? hovered.cells[hover.day] : null;
  const hoveredCase = hoveredCell && hoveredCell[2] >= 0 ? data.cases[hoveredCell[2]] : null;

  return (
    <div className="smap" ref={ref}>
      <svg width={svgW} height={svgH} role="img" aria-label={`Silence map: ${data.streams.length} services by day, ${day(data.dates[0])} to ${day(data.dates[n - 1])}`}>
        {months.map((m) => (
          <text key={m.i} className="axis-label" x={labelW + m.i * cellW} y={12}>
            {m.label}
          </text>
        ))}
        {data.streams.map((s, si) => {
          const y = top + si * rowH;
          return (
            <g key={s.stream_id}>
              <a href={href(`/cases?site_id=${encodeURIComponent(s.stream_id.split(":")[0])}`)}>
                <text className="row-label" x={0} y={y + cellH * 0.62}>
                  {truncate(s.site_name, compact ? 26 : 28)}
                </text>
                <text className="row-sub" x={0} y={y + cellH + (compact ? 9 : 7)}>
                  {service(s.service_type)}
                </text>
              </a>
              {s.cells.map((c, i) => {
                if (!c) return null;
                const f = fill(c[0], c[1]);
                const h = cellH * f.h;
                return (
                  <rect
                    key={i}
                    className="cell"
                    x={labelW + i * cellW}
                    y={y + (cellH - h) / 2}
                    width={Math.max(1, cellW - (cellW > 4 ? 1 : 0))}
                    height={h}
                    fill={f.fill}
                    fillOpacity={f.opacity}
                    rx={cellW > 6 ? 1.5 : 0}
                  />
                );
              })}
              {runs[si].map((r) => {
                const c = data.cases[r.ci];
                const css = CATEGORY_META[c.category].css;
                return (
                  <a key={`${r.ci}-${r.from}`} href={href(`/cases/${encodeURIComponent(c.case_id)}`)} aria-label={`Case ${c.case_id}: ${CATEGORY_META[c.category].label}`}>
                    <rect x={labelW + r.from * cellW} y={y + cellH + 2} width={Math.max(3, (r.to - r.from + 1) * cellW - 1)} height={3} rx={1.5} fill={`var(--${css})`} />
                  </a>
                );
              })}
              <rect
                x={labelW}
                y={y - 2}
                width={cellW * n}
                height={cellH + 8}
                fill="transparent"
                style={{ cursor: "crosshair" }}
                onMouseMove={(e) => {
                  const box = (e.currentTarget as SVGRectElement).getBoundingClientRect();
                  const i = Math.min(n - 1, Math.max(0, Math.floor(((e.clientX - box.left) / box.width) * n)));
                  setHover({ x: e.clientX, y: e.clientY, stream: si, day: i });
                }}
                onMouseLeave={() => setHover(null)}
                onClick={() => {
                  const cell = hover ? s.cells[hover.day] : null;
                  if (cell && cell[2] >= 0) navigate(`/cases/${encodeURIComponent(data.cases[cell[2]].case_id)}`);
                }}
              />
            </g>
          );
        })}
        {hover && (
          <rect
            x={labelW + hover.day * cellW - 0.5}
            y={top + hover.stream * rowH - 1.5}
            width={Math.max(2, cellW)}
            height={cellH + 3}
            fill="none"
            stroke="var(--ink)"
            strokeWidth={1.2}
            pointerEvents="none"
          />
        )}
      </svg>
      {hover && hovered && (
        <div className="tip float-tip" style={{ left: Math.min(hover.x + 14, window.innerWidth - 300), top: hover.y + 14 }}>
          <div className="t-head">
            {hovered.site_name} · {service(hovered.service_type)}
          </div>
          <div className="muted" style={{ marginBottom: 4 }}>
            {weekday(data.dates[hover.day])} {day(data.dates[hover.day])}
          </div>
          {hoveredCell ? (
            <>
              <div>{STATUS_LABELS[CODE_TO_STATUS[hoveredCell[0]]]}</div>
              {CODE_TO_STATUS[hoveredCell[0]] === "low" && <div>Shortfall vs expected: {pct(hoveredCell[1])}</div>}
              {hoveredCase && (
                <div className="t-note">
                  <span className="mono">{hoveredCase.case_id}</span> · {CATEGORY_META[hoveredCase.category].short}
                  <br />
                  Click to open the investigation
                </div>
              )}
            </>
          ) : (
            <div className="muted">Not monitored on this day</div>
          )}
        </div>
      )}
    </div>
  );
}

export function SilenceLegend() {
  return (
    <div className="legend" aria-label="Silence map legend">
      <span className="item">
        <span className="sw" style={{ background: "linear-gradient(90deg, color-mix(in oklab, var(--access) 35%, transparent), var(--access))" }} />
        Recorded activity below expected (darker = larger shortfall)
      </span>
      <span className="item">
        <span className="sw sw-unknown" />
        No trustworthy data — unknown, not zero
      </span>
      <span className="item">
        <span className="sw" style={{ background: "var(--surface-3)" }} />
        Within expected range
      </span>
      <span className="item">
        <span className="sw" style={{ background: "var(--expected)", opacity: 0.5 }} />
        Closed by schedule on a normally-open day
      </span>
      <span className="item">
        <span className="ln" style={{ background: "var(--access)", height: 3 }} />
        Investigation window (colour = triage category)
      </span>
    </div>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
