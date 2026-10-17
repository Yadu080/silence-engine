import { useMemo, useState } from "react";
import { ErrorState, LoadingBlock, Seg } from "../components/Bits";
import { CaseTable } from "../components/CaseTable";
import { IconArrow } from "../components/Icons";
import { SilenceChart, type CaseSpan } from "../components/SilenceChart";
import { SilenceLegend, SilenceMap } from "../components/SilenceMap";
import { qs } from "../lib/api";
import { addDays, day, num, pct, service, signedPct } from "../lib/format";
import { href, useApi } from "../lib/hooks";
import type { AggregateDay, CaseList, DayView, SilenceMapData, StreamInfo, Summary } from "../lib/types";

const RANGES = [
  { value: 42, label: "6 weeks" },
  { value: 91, label: "13 weeks" },
  { value: 182, label: "26 weeks" },
  { value: 0, label: "All" },
];

export function OverviewView() {
  const summary = useApi<Summary>("/api/summary?window=28");
  const streams = useApi<{ streams: StreamInfo[] }>("/api/sites");

  if (summary.error) return <ErrorState error={summary.error} onRetry={summary.reload} />;
  if (!summary.data || !streams.data) return <LoadingBlock rows={6} label="Loading overview" />;
  const s = summary.data;

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 44 }}>
      <Hero s={s} />
      <ActivitySection summary={s} streams={streams.data.streams} />
      <section className="section" aria-labelledby="queue-h">
        <div className="section-head">
          <div>
            <h2 id="queue-h">Investigation queue</h2>
            <p>Highest-priority hypotheses ending in the last {s.window_days} days. Priority is a ranking aid, not a probability.</p>
          </div>
          <a className="btn small" href={href("/cases")}>
            All investigations <IconArrow size={14} />
          </a>
        </div>
        {s.top_cases.length ? (
          <CaseTable items={s.top_cases} compact caption="Top recent investigations" />
        ) : (
          <p className="muted">No actionable investigations in this window.</p>
        )}
      </section>
      <MapPreview asOf={s.as_of} />
    </div>
  );
}

function Hero({ s }: { s: Summary }) {
  const L = s.ledger;
  const total = L.unaccounted_in_cases;
  const parts = [
    { key: "POTENTIAL_ACCESS_GAP", cls: "fill-access", label: "while feeds looked healthy — potential access gaps", v: L.by_category.POTENTIAL_ACCESS_GAP },
    { key: "REPORTING_PROBLEM", cls: "fill-reporting", label: "during data-feed problems — real activity unknown", v: L.by_category.REPORTING_PROBLEM },
    { key: "UNCERTAIN", cls: "fill-uncertain", label: "with mixed or insufficient evidence", v: L.by_category.UNCERTAIN },
  ];
  const share = (v: number) => (total ? v / total : 0);
  return (
    <section className="hero" aria-labelledby="hero-h">
      <div>
        <h1 id="hero-h" className="hero-statement">
          {total > 0 ? (
            <>
              About <span className="figure num">{num(total)}</span> expected service interactions did not appear in the records in the
              last {s.window_days} days.
            </>
          ) : (
            <>No unexplained silence in the last {s.window_days} days.</>
          )}
        </h1>
        <p className="hero-sub">
          SILENCE ENGINE compares what each service normally records with what actually arrived, then checks the data feed and the
          context before suggesting why. Every figure below is computed from the loaded dataset; every alert is a hypothesis to verify.
        </p>
        {total > 0 && (
          <div className="ledger">
            <div className="ledger-bar" role="img" aria-label={parts.map((p) => `${pct(share(p.v))} ${p.label}`).join("; ")}>
              {parts
                .filter((p) => p.v > 0)
                .map((p) => (
                  <span key={p.key} className={p.cls} style={{ flexGrow: p.v }} />
                ))}
            </div>
            <div className="ledger-keys">
              {parts.map((p) => (
                <div className="ledger-key" key={p.key}>
                  <span className={`sw ${p.cls}`} aria-hidden="true" />
                  <span>
                    <b className="num">{pct(share(p.v))}</b>
                    {num(p.v)} {p.label}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      <dl className="status-list" aria-label="Current status">
        <StatusRow label="Service locations monitored" sub={`${s.streams_monitored} service streams, aggregate daily counts`} value={num(s.sites_monitored)} />
        <StatusRow
          label="Investigations needing attention"
          sub="Actionable, recent, not yet reviewed"
          value={num(s.investigations_requiring_attention)}
          link="/cases?review=open"
        />
        <StatusRow
          label="Potential service-access gaps"
          sub="Healthy feed, activity well below expectation"
          value={num(s.potential_access_gaps)}
          link="/cases?category=POTENTIAL_ACCESS_GAP"
        />
        <StatusRow
          label="Data-feed anomalies"
          sub={`${s.reporting_problems} reporting-problem case${s.reporting_problems === 1 ? "" : "s"} · ${s.feeds_not_healthy} feed${s.feeds_not_healthy === 1 ? "" : "s"} not healthy now`}
          value={num(s.reporting_problems + s.feeds_not_healthy)}
          link="/data-quality"
        />
        <StatusRow
          label="Recorded activity vs expected, last 7 days"
          sub={`Trusted days only · previous 7 days ${signedPct(s.activity_change_prev7)}`}
          value={signedPct(s.activity_change_last7)}
          delta
        />
      </dl>
    </section>
  );
}

function StatusRow({ label, sub, value, link, delta }: { label: string; sub: string; value: string; link?: string; delta?: boolean }) {
  return (
    <div>
      <dt>
        {link ? <a href={href(link)}>{label}</a> : label}
        <small>{sub}</small>
      </dt>
      <dd className={`num ${delta ? "delta" : ""}`}>{value}</dd>
    </div>
  );
}

function ActivitySection({ summary, streams }: { summary: Summary; streams: StreamInfo[] }) {
  const defaultStream = summary.top_cases[0]?.stream_id ?? "ALL";
  const [stream, setStream] = useState<string>(defaultStream);
  const [span, setSpan] = useState<number>(91);
  const start = span ? addDays(summary.as_of, -(span - 1)) : null;
  const ts = useApi<{ days: DayView[] | AggregateDay[]; aggregate: boolean }>(`/api/timeseries${qs({ stream_id: stream, start })}`);
  const siteId = stream === "ALL" ? null : stream.split(":")[0];
  const cases = useApi<CaseList>(siteId ? `/api/cases${qs({ site_id: siteId, include_expected: true, limit: 500 })}` : null);
  const spans: CaseSpan[] = useMemo(
    () =>
      (cases.data?.items ?? [])
        .filter((c) => c.stream_id === stream && c.actionable)
        .map((c) => ({ case_id: c.case_id, category: c.category, onset: c.onset, end: c.end })),
    [cases.data, stream],
  );
  const info = streams.find((x) => x.stream_id === stream);

  return (
    <section className="section" aria-labelledby="act-h">
      <div className="section-head">
        <div>
          <h2 id="act-h">Expected versus observed activity</h2>
          <p>
            {stream === "ALL"
              ? "All monitored services combined. Hatched columns are expected activity on days with no trustworthy data."
              : `${info?.site_name ?? stream} · ${service(info?.service_type ?? "")}. Shaded windows are investigations — click one to open it.`}
          </p>
        </div>
      </div>
      <div className="toolbar">
        <label className="field">
          <span>Service</span>
          <select className="select" value={stream} onChange={(e) => setStream(e.target.value)}>
            <option value="ALL">All services (aggregate)</option>
            {streams.map((x) => (
              <option key={x.stream_id} value={x.stream_id}>
                {x.site_name} — {service(x.service_type)}
              </option>
            ))}
          </select>
        </label>
        <div className="field">
          <span>Period ending {day(summary.as_of)}</span>
          <Seg label="Period" value={span} onChange={setSpan} options={RANGES} />
        </div>
      </div>
      <div className={`chart-frame ${ts.refreshing ? "refreshing" : ""}`}>
        {ts.error ? (
          <ErrorState error={ts.error} onRetry={ts.reload} />
        ) : !ts.data ? (
          <LoadingBlock rows={5} label="Loading chart" />
        ) : ts.data.aggregate ? (
          <SilenceChart aggregate={ts.data.days as AggregateDay[]} caption="Expected versus observed activity, all services" />
        ) : (
          <SilenceChart
            days={ts.data.days as DayView[]}
            cases={spans}
            caption={`Expected versus observed activity for ${info?.site_name ?? stream}`}
          />
        )}
      </div>
    </section>
  );
}

function MapPreview({ asOf }: { asOf: string }) {
  const start = addDays(asOf, -55);
  const map = useApi<SilenceMapData>(`/api/silence-map${qs({ start })}`);
  return (
    <section className="section" aria-labelledby="map-h">
      <div className="section-head">
        <div>
          <h2 id="map-h">The silence map — last 8 weeks</h2>
          <p>Each row is a service, each cell a day. Colour shows where expected activity did not appear; hatching means we simply do not know.</p>
        </div>
        <a className="btn small" href={href("/silence")}>
          Full silence map <IconArrow size={14} />
        </a>
      </div>
      {map.error ? (
        <ErrorState error={map.error} onRetry={map.reload} />
      ) : !map.data ? (
        <LoadingBlock rows={6} label="Loading silence map" />
      ) : (
        <div className="panel panel-pad" style={{ display: "grid", gap: 14 }}>
          <SilenceMap data={map.data} compact />
          <SilenceLegend />
        </div>
      )}
    </section>
  );
}
