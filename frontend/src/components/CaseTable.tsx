import { num, pct, range, service } from "../lib/format";
import { href, navigate } from "../lib/hooks";
import type { CaseBrief } from "../lib/types";
import { CategoryBadge, ContextIcons, FeedBadge, Meter, ReviewBadge } from "./Bits";

export type SortKey = "priority" | "onset" | "shortfall" | "evidence" | "duration";

export function CaseTable({
  items,
  compact = false,
  sort,
  descending,
  onSort,
  caption,
}: {
  items: CaseBrief[];
  compact?: boolean;
  sort?: SortKey;
  descending?: boolean;
  onSort?: (k: SortKey) => void;
  caption: string;
}) {
  const head = (key: SortKey | null, label: string, cls = "") => {
    if (!key || !onSort) return <th className={cls}>{label}</th>;
    const active = sort === key;
    return (
      <th className={`sortable ${cls}`} aria-sort={active ? (descending ? "descending" : "ascending") : "none"}>
        <button type="button" onClick={() => onSort(key)}>
          {label}
          {active ? (descending ? " ↓" : " ↑") : ""}
        </button>
      </th>
    );
  };
  return (
    <div className="table-wrap">
      <table className="data cases-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {head("priority", "Priority")}
            {head(null, "Investigation")}
            {head("onset", "Window")}
            {head(null, "Expected → recorded", "r")}
            {head("shortfall", "Unaccounted", "r")}
            {head(null, "Feed")}
            {head(null, "Context")}
            {head(null, "Triage")}
            {!compact && head(null, "Review")}
          </tr>
        </thead>
        <tbody>
          {items.map((c) => {
            const link = `/cases/${encodeURIComponent(c.case_id)}`;
            const unknownDays = c.feed_status !== "HEALTHY";
            return (
              <tr
                key={c.case_id}
                className="click"
                onClick={(e) => {
                  if ((e.target as HTMLElement).closest("a")) return;
                  navigate(link);
                }}
              >
                <td data-label="Priority">
                  <Meter value={c.priority} label="Priority" />
                </td>
                <td data-label="Case">
                  <a className="case-link" href={href(link)}>
                    {c.site_name}
                  </a>
                  <span className="sub">
                    {service(c.service_type)} · <span className="mono">{c.case_id}</span>
                  </span>
                </td>
                <td data-label="Window">
                  {range(c.onset, c.end)}
                  <span className="sub">
                    {c.ongoing ? "Ongoing · " : ""}
                    {c.duration_days} day{c.duration_days === 1 ? "" : "s"}
                  </span>
                </td>
                <td data-label="Expected → recorded" className="r num">
                  {c.kind === "closure" ? (
                    <span className="muted">Closed by schedule</span>
                  ) : (
                    <>
                      ≈{num(c.expected_total)} → {num(c.reported_total)}
                      {unknownDays && <span className="sub">incl. days with no trustworthy count</span>}
                    </>
                  )}
                </td>
                <td data-label="Unaccounted" className="r num">
                  {c.kind === "closure" ? "—" : num(c.unaccounted_total)}
                  {c.shortfall_frac !== null && c.kind !== "closure" && (
                    <span className="sub">{pct(c.shortfall_frac)} below on trusted days</span>
                  )}
                </td>
                <td data-label="Feed">
                  <FeedBadge status={c.feed_status} />
                </td>
                <td data-label="Context">
                  <ContextIcons
                    weather={c.context_flags.severe_weather_days}
                    transport={c.context_flags.transport_days}
                    outage={c.context_flags.outage_days}
                  />
                </td>
                <td data-label="Triage">
                  <CategoryBadge category={c.category} short />
                </td>
                {!compact && (
                  <td data-label="Review">
                    <ReviewBadge status={c.review_status} />
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
