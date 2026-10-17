import { useState } from "react";
import { ErrorState, LoadingBlock, Notice, Seg } from "../components/Bits";
import { CaseTable } from "../components/CaseTable";
import { SilenceLegend, SilenceMap } from "../components/SilenceMap";
import { qs } from "../lib/api";
import { addDays, day } from "../lib/format";
import { useApi } from "../lib/hooks";
import type { CaseList, Health, SilenceMapData } from "../lib/types";

const RANGES = [
  { value: 56, label: "8 weeks" },
  { value: 182, label: "26 weeks" },
  { value: 0, label: "Full period" },
];

export function SilenceMapView() {
  const health = useApi<Health>("/api/health");
  const [span, setSpan] = useState(182);
  const asOf = health.data?.as_of ?? null;
  const start = asOf && span ? addDays(asOf, -(span - 1)) : null;
  const map = useApi<SilenceMapData>(asOf ? `/api/silence-map${qs({ start })}` : null);
  const cases = useApi<CaseList>(asOf ? `/api/cases${qs({ since: start, limit: 500 })}` : null);

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 28 }}>
      <div className="page-head">
        <div>
          <h1>The silence map</h1>
          <p className="lede">
            Where did expected activity fail to appear? Amber cells are days when trustworthy counts fell below what comparable past days
            predicted. Hatched cells are days when we have no trustworthy count at all — those are reporting questions, not evidence that
            nobody was served.
          </p>
        </div>
        <Seg label="Period" value={span} onChange={setSpan} options={RANGES} />
      </div>
      {map.error ? (
        <ErrorState error={map.error} onRetry={map.reload} />
      ) : !map.data ? (
        <LoadingBlock rows={8} label="Loading silence map" />
      ) : (
        <div className={`panel panel-pad chart-frame ${map.refreshing ? "refreshing" : ""}`} style={{ display: "grid", gap: 16 }}>
          <SilenceMap data={map.data} />
          <SilenceLegend />
          <p className="muted" style={{ fontSize: 12.5 }}>
            {map.data.dates.length} days from {day(map.data.dates[0])} to {day(map.data.dates[map.data.dates.length - 1])}. Hover a cell for
            detail; click a cell or an underline to open its investigation. Rows link to that location’s cases.
          </p>
        </div>
      )}
      <Notice>
        A gap in recorded activity is a reason to ask a question, not an answer. The engine separates missing data from observed zeros,
        but it cannot see why people did or did not use a service.
      </Notice>
      <section className="section" aria-labelledby="inrange-h">
        <div className="section-head">
          <h2 id="inrange-h">Investigations in this period</h2>
          <p>{cases.data ? `${cases.data.total} actionable` : ""}</p>
        </div>
        {cases.error ? (
          <ErrorState error={cases.error} onRetry={cases.reload} />
        ) : !cases.data ? (
          <LoadingBlock rows={4} />
        ) : (
          <CaseTable items={cases.data.items} caption="Investigations in the selected period" />
        )}
      </section>
    </div>
  );
}
