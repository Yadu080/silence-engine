import { useMemo, useState } from "react";
import { Empty, ErrorState, LoadingBlock } from "../components/Bits";
import { CaseTable, type SortKey } from "../components/CaseTable";
import { qs } from "../lib/api";
import { CATEGORY_META, CATEGORY_ORDER, service } from "../lib/format";
import { navigate, useApi } from "../lib/hooks";
import type { CaseList, Category, StreamInfo } from "../lib/types";

const PAGE = 50;

export function InvestigationsView({ query }: { query: URLSearchParams }) {
  const categories = (query.get("category") ?? "").split(",").filter(Boolean) as Category[];
  const site = query.get("site_id") ?? "";
  const feed = query.get("feed_status") ?? "";
  const review = query.get("review") ?? "";
  const ongoing = query.get("ongoing") ?? "";
  const sort = (query.get("sort") as SortKey) || "priority";
  const order = query.get("order") === "asc" ? "asc" : "desc";
  const [limit, setLimit] = useState(PAGE);

  const params = {
    category: categories.join(","),
    site_id: site,
    feed_status: feed,
    review,
    ongoing,
    sort,
    order,
    limit,
    include_expected: categories.length === 0 && query.get("include_expected") === "true" ? true : undefined,
  };
  const list = useApi<CaseList>(`/api/cases${qs(params)}`);
  const sites = useApi<{ streams: StreamInfo[] }>("/api/sites");
  const siteOptions = useMemo(() => {
    const m = new Map<string, string>();
    sites.data?.streams.forEach((s) => m.set(s.site_id, s.site_name));
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [sites.data]);

  function update(patch: Record<string, string | null>) {
    const next = new URLSearchParams(query);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, v);
    }
    setLimit(PAGE);
    const s = next.toString();
    navigate(`/cases${s ? `?${s}` : ""}`);
  }

  function toggleCategory(c: Category) {
    const set = new Set(categories);
    if (set.has(c)) set.delete(c);
    else set.add(c);
    update({ category: [...set].join(",") || null, include_expected: null });
  }

  const totals = list.data?.category_totals;
  const active = categories.length > 0 || site || feed || review || ongoing;

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div className="page-head">
        <div>
          <h1>Investigations</h1>
          <p className="lede">
            Every detected silence, triaged into a hypothesis. Expected-variation notes (schedule closures, ordinary dips) are hidden unless
            you ask for them. Open a case to see the evidence for and against each explanation.
          </p>
        </div>
      </div>

      <div className="toolbar" role="search" aria-label="Filter investigations">
        <div className="field">
          <span>Triage category</span>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {CATEGORY_ORDER.map((c) => (
              <button key={c} type="button" className="chip" aria-pressed={categories.includes(c)} onClick={() => toggleCategory(c)}>
                <span className={`dot`} style={{ ["--c" as string]: `var(--${CATEGORY_META[c].css})` }} aria-hidden="true" />
                {CATEGORY_META[c].short}
                {totals && <span className="n">{totals[c]}</span>}
              </button>
            ))}
          </div>
        </div>
        <label className="field">
          <span>Location</span>
          <select className="select" value={site} onChange={(e) => update({ site_id: e.target.value })}>
            <option value="">All locations</option>
            {siteOptions.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Feed health</span>
          <select className="select" value={feed} onChange={(e) => update({ feed_status: e.target.value })}>
            <option value="">Any</option>
            <option value="HEALTHY">Healthy</option>
            <option value="DEGRADED">Degraded</option>
            <option value="FAILED">Failed</option>
          </select>
        </label>
        <label className="field">
          <span>Review</span>
          <select className="select" value={review} onChange={(e) => update({ review: e.target.value })}>
            <option value="">Any</option>
            <option value="open">Not yet reviewed</option>
            <option value="reviewed">Reviewed</option>
          </select>
        </label>
        <label className="field">
          <span>State</span>
          <select className="select" value={ongoing} onChange={(e) => update({ ongoing: e.target.value })}>
            <option value="">Any</option>
            <option value="true">Ongoing</option>
            <option value="false">Ended</option>
          </select>
        </label>
        {categories.length === 0 && (
          <label className="field" style={{ alignSelf: "center", display: "flex", gap: 8, alignItems: "center", marginTop: 18 }}>
            <input
              type="checkbox"
              checked={query.get("include_expected") === "true"}
              onChange={(e) => update({ include_expected: e.target.checked ? "true" : null })}
            />
            <span style={{ fontWeight: 400, color: "var(--ink-2)" }}>Include expected variation</span>
          </label>
        )}
        {active && (
          <button className="btn ghost small" style={{ marginTop: 18 }} onClick={() => navigate("/cases")}>
            Clear filters
          </button>
        )}
      </div>

      {list.error ? (
        <ErrorState error={list.error} onRetry={list.reload} />
      ) : !list.data ? (
        <LoadingBlock rows={8} label="Loading investigations" />
      ) : list.data.items.length === 0 ? (
        <Empty title="No investigations match these filters">
          Try clearing a filter. If you filtered by a location, it may simply have had no detected silences — which is the expected state.
        </Empty>
      ) : (
        <div className={`chart-frame ${list.refreshing ? "refreshing" : ""}`} style={{ display: "grid", gap: 12 }}>
          <p className="muted" style={{ fontSize: 13 }}>
            Showing {list.data.items.length} of {list.data.total}
            {site && sites.data ? ` at ${siteOptions.find((s) => s[0] === site)?.[1] ?? site}` : ""}
            {categories.length ? ` · ${categories.map((c) => CATEGORY_META[c].short).join(", ")}` : ""}. Sorted by {sort}
            {order === "asc" ? " (ascending)" : ""}.
          </p>
          <CaseTable
            items={list.data.items}
            sort={sort}
            descending={order === "desc"}
            onSort={(k) => update({ sort: k, order: sort === k && order === "desc" ? "asc" : "desc" })}
            caption="Investigation queue"
          />
          {list.data.total > list.data.items.length && (
            <div>
              <button className="btn" onClick={() => setLimit((l) => l + PAGE)}>
                Show {Math.min(PAGE, list.data.total - list.data.items.length)} more
              </button>
            </div>
          )}
        </div>
      )}
      <p className="muted" style={{ fontSize: 12.5 }}>
        Streams: {sites.data?.streams.map((s) => `${s.site_name} (${service(s.service_type)})`).join(" · ")}
      </p>
    </div>
  );
}
