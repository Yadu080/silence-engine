import { useState } from "react";
import { CategoryBadge, ContextIcons, ErrorState, FeedBadge, LoadingBlock, Notice } from "../components/Bits";
import { IconBack } from "../components/Icons";
import { DayStrips, SilenceChart } from "../components/SilenceChart";
import { ApiError, api } from "../lib/api";
import { FLAG_LABELS, REVIEW_LABELS, dateTime, day, num, pct, range, service } from "../lib/format";
import { href, useApi, useLocalState } from "../lib/hooks";
import type { CaseDetail, CaseFull, Evidence, Hypothesis, Review } from "../lib/types";

const HYP_FOR_CATEGORY: Record<string, string> = {
  POTENTIAL_ACCESS_GAP: "ACCESS",
  REPORTING_PROBLEM: "REPORTING",
  EXPECTED_VARIATION: "NORMAL",
  UNCERTAIN: "",
};

export function CaseView({ caseId }: { caseId: string }) {
  const detail = useApi<CaseDetail>(`/api/cases/${encodeURIComponent(caseId)}`);
  if (detail.error) {
    return (
      <div style={{ display: "grid", gap: 16 }}>
        <BackLink />
        <ErrorState error={detail.error} onRetry={detail.error.status === 404 ? undefined : detail.reload} />
        {detail.error.status === 404 && (
          <p className="muted">Case identifiers change when the data is regenerated. Return to the queue to pick a current case.</p>
        )}
      </div>
    );
  }
  if (!detail.data) return <LoadingBlock rows={10} label="Loading investigation" />;
  const { case: c, timeline } = detail.data;

  return (
    <article className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 32 }}>
      <header className="case-head">
        <BackLink />
        <div className="badges">
          <CategoryBadge category={c.category} />
          <FeedBadge status={c.feed_status} />
          <span className="badge plain">
            Priority {c.priority} · {c.priority_band}
          </span>
          <span className="badge plain">
            Evidence {c.evidence_score} · {c.evidence_band}
          </span>
          {c.ongoing && <span className="badge plain">Ongoing</span>}
        </div>
        <h1>{c.headline}</h1>
        <div className="case-meta">
          <span>
            <b>{c.site_name}</b> · {service(c.service_type)}
            {c.region ? ` · ${c.region} region` : ""}
          </span>
          <span>
            Window <b>{range(c.onset, c.end)}</b> ({c.duration_days} days, {c.open_days} open)
          </span>
          {c.alert_date && (
            <span>
              Alert raised <b>{day(c.alert_date)}</b>
            </span>
          )}
          <span className="mono muted">{c.case_id}</span>
        </div>
        <Notice>
          This is an investigation hypothesis generated from aggregate, synthetic data. It is not a finding, and it says nothing about any
          individual. Verify with the service before acting.
        </Notice>
      </header>

      <section className="section" aria-labelledby="tl-h">
        <div className="section-head">
          <div>
            <h2 id="tl-h">What should have happened, and what was recorded</h2>
            <p>
              Five weeks before the window to two weeks after. The shaded band is the investigation window
              {c.onset !== c.start ? "; the CUSUM change-point estimate places the start of the decline earlier than the first flagged day" : ""}.
            </p>
          </div>
        </div>
        <div className="panel panel-pad" style={{ display: "grid", gap: 8 }}>
          <SilenceChart
            days={timeline}
            cases={[{ case_id: c.case_id, category: c.category, onset: c.onset, end: c.end }]}
            focusCase={{ case_id: c.case_id, category: c.category, onset: c.onset, end: c.end }}
            height={280}
            caption={`Expected versus observed activity around case ${c.case_id}`}
          />
          <DayStrips days={timeline} />
        </div>
        <Facts c={c} />
      </section>

      <div className="two-col">
        <div className="stack">
          <section className="section" aria-labelledby="why-h">
            <h2 id="why-h">Why this was flagged</h2>
            <p className="prose" style={{ fontSize: 15 }}>
              {c.explanation}
            </p>
            {c.kind === "silence" && (
              <p className="prose">
                Expected activity for each day is the median of the same weekday over the previous eight weeks, using only days whose
                feed was trustworthy at the time. A day is flagged when its count is well below that baseline (robust z-score and
                percentage shortfall), when a cumulative-sum test detects a persistent smaller decline, or when no trustworthy count
                arrived on a scheduled-open day.
              </p>
            )}
          </section>

          <section className="section" aria-labelledby="hyp-h">
            <div className="section-head">
              <h2 id="hyp-h">Competing explanations</h2>
              <p>Evidence weighed against each hypothesis</p>
            </div>
            <div className="hypotheses">
              {c.hypotheses.map((h) => (
                <HypothesisRow key={h.key} h={h} assigned={HYP_FOR_CATEGORY[c.category] === h.key} />
              ))}
            </div>
            {c.category === "UNCERTAIN" && (
              <p className="muted" style={{ fontSize: 13 }}>
                No single explanation is clearly favoured, which is why this case is marked uncertain.
              </p>
            )}
          </section>

          <section className="section" aria-labelledby="ev-h">
            <h2 id="ev-h">Evidence</h2>
            <EvidenceList title="Supports the assigned category" items={c.supporting} mark="+" />
            {c.conflicting.length > 0 && <EvidenceList title="Conflicts with it" items={c.conflicting} mark="−" />}
            <EvidenceList
              title="Other observations"
              items={c.evidence.filter((e) => !c.supporting.some((x) => x.text === e.text) && !c.conflicting.some((x) => x.text === e.text))}
              mark="·"
            />
          </section>

          <section className="section" aria-labelledby="lim-h">
            <h2 id="lim-h">Limitations of this evidence</h2>
            <ul className="limits">
              {c.limitations.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </section>
        </div>

        <div className="stack">
          <Verification c={c} />
          <ReviewPanel caseId={c.case_id} reviews={detail.data.reviews} statuses={detail.data.review_statuses} onSaved={detail.reload} />
          <Scores c={c} />
          <ContextPanel c={c} />
          <section className="section" aria-labelledby="audit-h">
            <h2 id="audit-h">Audit trail</h2>
            {detail.data.audit.length === 0 ? (
              <p className="muted">No actions recorded for this case yet.</p>
            ) : (
              <ol className="timeline-list">
                {detail.data.audit.map((a) => (
                  <li key={a.id}>
                    <span className="tdot" aria-hidden="true" />
                    <span>
                      <span className="when">{dateTime(a.ts)}</span>
                      <br />
                      {a.action.replace(/_/g, " ")} — {String((a.details as { status?: string }).status ?? "")}{" "}
                      <span className="hash">#{a.hash.slice(0, 10)}</span>
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </section>
          {detail.data.related.length > 0 && (
            <section className="section" aria-labelledby="rel-h">
              <h2 id="rel-h">Other cases for this service</h2>
              <ul className="limits" style={{ listStyle: "none", paddingLeft: 0 }}>
                {detail.data.related.map((r) => (
                  <li key={r.case_id} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <CategoryBadge category={r.category} short />
                    <a href={href(`/cases/${encodeURIComponent(r.case_id)}`)}>{range(r.onset, r.end)}</a>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </div>
    </article>
  );
}

function BackLink() {
  return (
    <a className="btn ghost small" href={href("/cases")} style={{ width: "fit-content", marginLeft: -10 }}>
      <IconBack size={14} /> Investigations
    </a>
  );
}

function Facts({ c }: { c: CaseFull }) {
  if (c.kind === "closure") {
    return (
      <dl className="kv">
        <dt>Schedule</dt>
        <dd>Closed on normally-open days — no investigation needed.</dd>
      </dl>
    );
  }
  return (
    <dl className="kv" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, auto) minmax(140px, 1fr))" }}>
      <dt>Expected over window</dt>
      <dd className="num">≈{num(c.expected_total)}</dd>
      <dt>Recorded (all counts)</dt>
      <dd className="num">{num(c.reported_total)}</dd>
      <dt>Trusted days: expected → observed</dt>
      <dd className="num">
        ≈{num(c.trusted_expected)} → {num(c.trusted_observed)} {c.shortfall_frac !== null && <span className="muted">({pct(c.shortfall_frac)} below)</span>}
      </dd>
      <dt>Expected on days with no trustworthy data</dt>
      <dd className="num">≈{num(c.missing_expected)}</dd>
      <dt>Aggregate deviation (z)</dt>
      <dd className="num">{c.z_agg === null ? "—" : c.z_agg.toFixed(1)}</dd>
      <dt>Comparable past days</dt>
      <dd className="num">{c.history_median} (median per day)</dd>
    </dl>
  );
}

function HypothesisRow({ h, assigned }: { h: Hypothesis; assigned: boolean }) {
  const cls =
    h.verdict === "Supported" ? "v-supported" : h.verdict === "Partly supported" ? "v-partly" : h.verdict === "Contested" ? "v-weak" : h.verdict === "Not supported" ? "v-not" : "v-cannot";
  return (
    <div className={`hyp ${assigned ? "assigned" : ""}`}>
      <h3>
        {h.label}
        {assigned && <span className="muted" style={{ fontWeight: 400 }}> — assigned</span>}
      </h3>
      <span className={`verdict ${cls}`}>{h.verdict}</span>
      <span className="muted" style={{ fontSize: 12.5 }}>
        {h.supporting.length} supporting · {h.contradicting.length} contradicting
      </span>
      {(h.supporting.length > 0 || h.contradicting.length > 0) && (
        <details>
          <summary>Show evidence</summary>
          {h.supporting.length > 0 && (
            <>
              <h4 style={{ marginTop: 8 }}>For</h4>
              <ul>
                {h.supporting.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </>
          )}
          {h.contradicting.length > 0 && (
            <>
              <h4 style={{ marginTop: 8 }}>Against</h4>
              <ul>
                {h.contradicting.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </>
          )}
        </details>
      )}
    </div>
  );
}

function EvidenceList({ title, items, mark }: { title: string; items: Evidence[]; mark: string }) {
  if (!items.length) return null;
  return (
    <div style={{ display: "grid", gap: 4 }}>
      <h3 style={{ fontSize: 13, color: "var(--muted)", fontWeight: 500 }}>{title}</h3>
      <ul className="evidence-list">
        {items.map((e) => (
          <li key={e.text}>
            <span className="mark" aria-hidden="true">
              {mark}
            </span>
            <span>
              {e.text}
              <span className="strength">
                {e.kind} · {e.strength}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Verification({ c }: { c: CaseFull }) {
  const [done, setDone] = useLocalState<number[]>(`silence-check-${c.case_id}`, []);
  return (
    <section className="section" aria-labelledby="ver-h">
      <div className="section-head">
        <h2 id="ver-h">Recommended verification</h2>
        <p>
          {done.length}/{c.verification_steps.length} checked
        </p>
      </div>
      <ul className="steps">
        {c.verification_steps.map((s, i) => {
          const checked = done.includes(i);
          return (
            <li key={s}>
              <label>
                <input type="checkbox" checked={checked} onChange={() => setDone(checked ? done.filter((x) => x !== i) : [...done, i])} />
                <span className={checked ? "done" : ""}>{s}</span>
              </label>
            </li>
          );
        })}
      </ul>
      <p className="muted" style={{ fontSize: 12 }}>
        Checklist ticks are kept in this browser only. Record the outcome below so it enters the audit trail.
      </p>
    </section>
  );
}

function ReviewPanel({ caseId, reviews, statuses, onSaved }: { caseId: string; reviews: Review[]; statuses: string[]; onSaved: () => void }) {
  const [status, setStatus] = useState(statuses[0]);
  const [role, setRole] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setOk(false);
    try {
      await api.post(`/api/cases/${encodeURIComponent(caseId)}/review`, { status, note: note || undefined, reviewer_role: role || undefined });
      setNote("");
      setOk(true);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message}${err.status === 401 || err.status === 403 ? " Set an API token on the Platform page if this server requires one." : ""}` : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="section" aria-labelledby="rev-h">
      <h2 id="rev-h">Human review</h2>
      <form className="panel panel-pad" style={{ display: "grid", gap: 12 }} onSubmit={save}>
        <label className="field">
          <span>Outcome</span>
          <select className="select" value={status} onChange={(e) => setStatus(e.target.value)}>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {REVIEW_LABELS[s] ?? s}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Reviewer role (optional)</span>
          <input className="input" maxLength={60} value={role} onChange={(e) => setRole(e.target.value)} placeholder="e.g. Regional duty analyst" />
        </label>
        <label className="field">
          <span>Note (optional)</span>
          <textarea className="textarea" maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What was checked, and what was found. Do not enter personal information." />
        </label>
        {error && <Notice tone="error">{error}</Notice>}
        {ok && <p className="muted" role="status">Review recorded and added to the audit chain.</p>}
        <div>
          <button className="btn primary" type="submit" disabled={saving}>
            {saving ? "Saving…" : "Record review"}
          </button>
        </div>
      </form>
      {reviews.length > 0 && (
        <ol className="timeline-list" aria-label="Review history">
          {reviews.map((r) => (
            <li key={r.id}>
              <span className="tdot" aria-hidden="true" />
              <span>
                <b>{REVIEW_LABELS[r.status] ?? r.status}</b>
                {r.reviewer_role ? ` · ${r.reviewer_role}` : ""}
                <br />
                <span className="when">{dateTime(r.created_at)}</span>
                {r.note && <p style={{ marginTop: 4, color: "var(--ink-2)" }}>{r.note}</p>}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function Scores({ c }: { c: CaseFull }) {
  const rows = (comp: Record<string, number | string>) =>
    Object.entries(comp).filter(([k, v]) => k !== "formula" && typeof v === "number") as [string, number][];
  return (
    <section className="section" aria-labelledby="sc-h">
      <h2 id="sc-h">How the scores are built</h2>
      <p className="muted" style={{ fontSize: 13 }}>
        Neither score is a probability. They are transparent heuristics; the evaluation page shows how triage accuracy varies by
        evidence band on synthetic test data.
      </p>
      {[
        { title: `Investigation priority · ${c.priority}`, comp: c.priority_components },
        { title: `Evidence sufficiency · ${c.evidence_score}`, comp: c.evidence_components },
      ].map((block) => (
        <div key={block.title} className="panel panel-pad" style={{ display: "grid", gap: 10 }}>
          <h3>{block.title}</h3>
          {rows(block.comp).map(([k, v]) => (
            <div className="score-row" key={k}>
              <span className="ink2">{k.replace(/_/g, " ")}</span>
              <span className="num">{v.toFixed(2)}</span>
              <span className="bar">
                <span style={{ width: `${Math.min(100, v * 100)}%` }} />
              </span>
            </div>
          ))}
          <p className="formula">{String(block.comp.formula)}</p>
        </div>
      ))}
    </section>
  );
}

function ContextPanel({ c }: { c: CaseFull }) {
  const flags = Object.entries(c.flag_days).filter(([k]) => k !== "late_arrival");
  return (
    <section className="section" aria-labelledby="ctx-h">
      <h2 id="ctx-h">Feed health and context</h2>
      <dl className="kv">
        <dt>Feed status</dt>
        <dd>
          <FeedBadge status={c.feed_status} /> <span className="muted">{pct(c.feed_problem_frac)} of open days affected</span>
        </dd>
        <dt>Feed flags</dt>
        <dd>{flags.length ? flags.map(([k, v]) => `${FLAG_LABELS[k] ?? k} (${v}d)`).join(", ") : "None"}</dd>
        <dt>Context, {range(c.context.window_start, c.context.window_end)}</dt>
        <dd>
          <ContextIcons weather={c.context.severe_weather_days} transport={c.context.transport_days} outage={c.context.outage_days} />
        </dd>
        <dt>Historical precedent</dt>
        <dd>
          {c.precedent_rate === null
            ? "Not enough ordinary history to compare"
            : `${pct(c.precedent_rate)} of ${c.precedent_windows} earlier windows had a comparable shortfall`}
        </dd>
        <dt>Comparable services</dt>
        <dd>
          {c.peers.compared === 0
            ? "None at the same site or region"
            : c.peers.peers.map((p) => `${p.site_name} ${service(p.service_type).toLowerCase()} ${p.shortfall_frac >= 0 ? `${pct(p.shortfall_frac)} below` : `${pct(-p.shortfall_frac)} above`}`).join("; ")}
        </dd>
      </dl>
    </section>
  );
}
