import { CATEGORY_META, TRUTH_LABELS } from "../lib/format";
import type { Category, PeriodMetrics } from "../lib/types";

export function Confusion({ title, m }: { title: string; m: PeriodMetrics }) {
  const cm = m.explanation.confusion;
  const max = Math.max(1, ...cm.matrix.flat());
  const correct = (row: string, col: string) =>
    (row === "ACCESS_GAP" && col === "POTENTIAL_ACCESS_GAP") ||
    (row === "REPORTING_FAILURE" && col === "REPORTING_PROBLEM") ||
    (row === "AMBIGUOUS" && col === "UNCERTAIN") ||
    ((row === "SCHEDULED_CLOSURE" || row === "NORMAL_VARIATION") && (col === "EXPECTED_VARIATION" || col === "NOT_FLAGGED"));
  const colLabel = (c: string) => (c === "NOT_FLAGGED" ? "Not flagged" : CATEGORY_META[c as Category]?.short ?? c);
  return (
    <figure className="panel panel-pad" style={{ margin: 0, display: "grid", gap: 10 }}>
      <figcaption>
        <h3>{title}</h3>
      </figcaption>
      <div className="cm" style={{ gridTemplateColumns: `minmax(110px, 1.2fr) repeat(${cm.cols.length}, minmax(0, 1fr))` }} role="table" aria-label={`Confusion matrix: ${title}`}>
        <div className="hdr" role="columnheader">
          True ↓ / Reported →
        </div>
        {cm.cols.map((c) => (
          <div className="hdr" key={c} role="columnheader">
            {colLabel(c)}
          </div>
        ))}
        {cm.rows.map((row, ri) => (
          <div key={row} style={{ display: "contents" }} role="row">
            <div className="rhdr" role="rowheader">
              {TRUTH_LABELS[row]}
            </div>
            {cm.matrix[ri].map((v, ci) => {
              const na = row === "NO_EVENT" && cm.cols[ci] === "NOT_FLAGGED";
              const strength = v / max;
              return (
                <div
                  key={ci}
                  role="cell"
                  className={`cell ${correct(row, cm.cols[ci]) && v > 0 ? "correct" : ""}`}
                  style={{
                    background: na ? "transparent" : `color-mix(in oklab, var(--ink) ${Math.round(6 + strength * 52)}%, var(--surface))`,
                    color: strength > 0.55 ? "var(--inverse)" : "var(--ink)",
                  }}
                  title={`${TRUTH_LABELS[row]} reported as ${colLabel(cm.cols[ci])}: ${v}`}
                >
                  {na ? "" : v}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </figure>
  );
}
