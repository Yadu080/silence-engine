import { useState } from "react";
import { CartesianGrid, LabelList, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, ErrorState, LoadingBlock, Notice } from "../components/Bits";
import { Confusion } from "../components/ConfusionMatrix";
import { MlTriageSection } from "./MlTriageSection";
import { IconRefresh } from "../components/Icons";
import { ApiError, api, requestHealthRefresh } from "../lib/api";
import { CATEGORY_META, TRUTH_LABELS, dateTime, day, num, pct, shortHash } from "../lib/format";
import { useApi } from "../lib/hooks";
import type { Category, EvaluationReport, EvaluationResponse, Health, Method, PeriodMetrics } from "../lib/types";

const METHODS: Method[] = ["naive", "robust", "engine"];
const SHORT: Record<Method, string> = { naive: "Naive % drop", robust: "Detector only (ablation)", engine: "SILENCE ENGINE" };
const LINE: Record<Method, string> = { naive: "var(--expected)", robust: "color-mix(in oklab, var(--expected) 55%, var(--ink))", engine: "var(--ink)" };

export function EvaluationView({ health }: { health: Health }) {
  const ev = useApi<EvaluationResponse>("/api/evaluation");
  const [runError, setRunError] = useState<string | null>(null);
  const running = health.job?.status === "running";

  async function rerun() {
    setRunError(null);
    try {
      await api.post("/api/evaluation/run");
      requestHealthRefresh();
    } catch (e) {
      setRunError(e instanceof ApiError ? e.message : String(e));
    }
  }

  if (ev.error) return <ErrorState error={ev.error} onRetry={ev.reload} />;
  if (!ev.data) return <LoadingBlock rows={10} label="Loading evaluation" />;
  if (!ev.data.available || !ev.data.report) {
    return (
      <div style={{ display: "grid", gap: 20 }}>
        <h1>Evaluation</h1>
        <Empty title="No evaluation for the loaded data">{ev.data.reason}</Empty>
      </div>
    );
  }
  const r = ev.data.report;
  const t = r.test;

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 40 }}>
      <div className="page-head">
        <div>
          <h1>Does the method help?</h1>
          <p className="lede">
            Measured on held-out synthetic scenarios with known ground truth. Thresholds were tuned on the training period only; every
            number below is computed from the test period. Detecting a decline and explaining it are scored separately.
          </p>
        </div>
        <div style={{ display: "grid", gap: 6, justifyItems: "end" }}>
          <button className="btn" onClick={rerun} disabled={running}>
            <IconRefresh size={15} /> {running ? "Evaluation running…" : "Re-run evaluation"}
          </button>
          <span className="muted" style={{ fontSize: 12 }}>
            Last run {dateTime(r.generated_at)} on {r.platform?.machine} · {r.duration_seconds}s
          </span>
        </div>
      </div>
      {runError && <Notice tone="error">{runError}</Notice>}
      <Notice tone="warn">
        <strong>Synthetic test data.</strong> {r.notice} The generator and the triage rules were written by the same team, so these results
        show that the logic behaves as designed under noise, missing data and delays — they are not evidence of real-world accuracy.
      </Notice>

      <Protocol r={r} />

      <section className="section" aria-labelledby="head-h">
        <div className="section-head">
          <div>
            <h2 id="head-h">Headline comparison — test period</h2>
            <p>Bars show the point estimate; whiskers show 95% Wilson intervals where defined. Engine in ink, baselines in grey.</p>
          </div>
        </div>
        <div className="metric-rows">
          <MetricRow name="Event recall" note="Share of alertable events (access gaps, reporting failures, ambiguous) that raised any alert" t={t} pick={(m) => [m.detection.recall, m.detection.recall_ci]} detail={(m) => `${m.detection.detected}/${m.detection.alertable_events}`} />
          <MetricRow name="Alert precision" note="Share of alerts that overlapped a real alertable event" t={t} pick={(m) => [m.detection.precision, m.detection.precision_ci]} detail={(m) => `${m.detection.true_alerts}/${m.detection.alerts}`} />
          <MetricRow name="Explanation accuracy" note="Of detected events, share given the correct category" t={t} pick={(m) => [m.explanation.accuracy_given_detection, m.explanation.accuracy_ci]} detail={(m) => `${m.explanation.correct}/${m.explanation.detected}`} />
          <MetricRow name="Benign events left alone" note="Scheduled closures and ordinary dips that raised no actionable alert" t={t} pick={(m) => [m.detection.benign_handled_rate, null]} detail={(m) => `${m.detection.benign_events_not_alerted}/${m.detection.benign_events}`} />
          <MetricRow
            name="Reporting failures called access gaps"
            note="Lower is better: a broken feed misread as people not being served"
            t={t}
            pick={(m) => [m.explanation.reporting_failures_called_access_gaps, null]}
            detail={() => ""}
            lowerBetter
          />
          <MetricRow
            name="False alerts"
            note="Per 100 stream-weeks; lower is better"
            t={t}
            pick={(m) => [m.detection.false_alerts_per_100_stream_weeks, null]}
            detail={(m) => `${m.detection.false_alerts} alerts`}
            scale={Math.max(1, ...METHODS.map((k) => t[k].detection.false_alerts_per_100_stream_weeks ?? 0))}
            format={(v) => num(v, 1)}
            lowerBetter
          />
          <MetricRow
            name="Median detection delay"
            note="Days from event start to alert (retrospective data); lower is better"
            t={t}
            pick={(m) => [m.detection.delay_days.median, null]}
            detail={(m) => `p90 ${num(m.detection.delay_days.p90, 1)}`}
            scale={Math.max(1, ...METHODS.map((k) => t[k].detection.delay_days.p90 ?? 0))}
            format={(v) => `${num(v, 1)} d`}
            lowerBetter
          />
        </div>
        <Breakdown t={t} />
      </section>

      <section className="section" aria-labelledby="cm-h">
        <div className="section-head">
          <div>
            <h2 id="cm-h">Confusion matrices</h2>
            <p>Rows are the true synthetic event class; columns are what each method reported. Outlined cells are correct outcomes.</p>
          </div>
        </div>
        <div className="grid-auto" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(420px, 1fr))" }}>
          <Confusion title="SILENCE ENGINE" m={t.engine} />
          <Confusion title="Naive % drop baseline" m={t.naive} />
        </div>
      </section>

      <section className="section" aria-labelledby="pc-h">
        <h2 id="pc-h">Per-category precision and recall (engine, test period)</h2>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>True class → expected category</th>
                <th className="r">Events</th>
                <th className="r">Recall (95% CI)</th>
                <th className="r">Alerts with category</th>
                <th className="r">Precision (95% CI)</th>
              </tr>
            </thead>
            <tbody>
              {t.engine.explanation.per_class.map((p) => (
                <tr key={p.truth_class}>
                  <td>
                    {TRUTH_LABELS[p.truth_class]} → {CATEGORY_META[p.category as Category].label}
                  </td>
                  <td className="r num">{p.events}</td>
                  <td className="r num">
                    {pct(p.recall)} <span className="muted">{ci(p.recall_ci)}</span>
                  </td>
                  <td className="r num">{p.alerts_with_category}</td>
                  <td className="r num">
                    {pct(p.precision)} <span className="muted">{ci(p.precision_ci)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted" style={{ fontSize: 13 }}>
          Abstention: {pct(t.engine.explanation.abstention_rate_non_ambiguous)} of detected non-ambiguous events were marked uncertain rather
          than given a wrong label. Access gaps called reporting problems: {pct(t.engine.explanation.access_gaps_called_reporting)}.
        </p>
      </section>

      <MlTriageSection health={health} />

      <Subtypes m={t.engine} />
      <Tiers r={r} />
      <Robustness r={r} />
      <Replay r={r} />

      <section className="section" aria-labelledby="eb-h">
        <h2 id="eb-h">Is the evidence score informative?</h2>
        <p className="prose">
          Triage accuracy on detected test events, grouped by the engine’s evidence-sufficiency band. This is an empirical check, not a
          calibration: the score is not a probability.
        </p>
        <div className="table-wrap" style={{ maxWidth: 560 }}>
          <table className="data compact">
            <thead>
              <tr>
                <th>Evidence band</th>
                <th className="r">Events</th>
                <th className="r">Correct category</th>
              </tr>
            </thead>
            <tbody>
              {r.evidence_bands.map((b) => (
                <tr key={b.band}>
                  <td>{b.band}</td>
                  <td className="r num">{b.events}</td>
                  <td className="r num">
                    {pct(b.accuracy)} ({b.correct}/{b.events})
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <Tuning r={r} />

      <section className="section" aria-labelledby="repro-h">
        <h2 id="repro-h">Reproducibility</h2>
        <dl className="kv">
          <dt>Dataset fingerprint</dt>
          <dd className="hash">{r.dataset_fingerprint}</dd>
          <dt>Results hash</dt>
          <dd className="hash">{r.results_sha256}</dd>
          <dt>Engine version</dt>
          <dd>{r.engine_version}</dd>
          <dt>Observations used</dt>
          <dd>
            {num(r.observations_used)}
            {r.observations_excluded_unlabelled ? ` (${num(r.observations_excluded_unlabelled)} unlabelled rows excluded)` : ""}
          </dd>
        </dl>
        <p className="muted" style={{ fontSize: 13 }}>
          Run <code>python manage.py fingerprint</code> on another machine (for example the LinuxONE instance) with the same seed: identical
          hashes mean the same data produced the same metrics.
        </p>
      </section>
    </div>
  );
}

function ci(v: (number | null)[]): string {
  return v && v[0] !== null && v[1] !== null ? `(${pct(v[0])}–${pct(v[1])})` : "";
}

function Protocol({ r }: { r: EvaluationReport }) {
  const p = r.protocol;
  const span = (a: string, b: string) => (Date.parse(b) - Date.parse(a)) / 86400000 + 1;
  const total = span(p.start, p.test_end);
  const classes = ["ACCESS_GAP", "REPORTING_FAILURE", "AMBIGUOUS", "SCHEDULED_CLOSURE", "NORMAL_VARIATION"];
  return (
    <section className="section" aria-labelledby="proto-h">
      <h2 id="proto-h">Protocol</h2>
      <div className="protocol" role="img" aria-label={`Warm-up ${p.start} to ${p.warmup_end}; training ${p.train_start} to ${p.train_end}; test ${p.test_start} to ${p.test_end}`}>
        <div className="warm" style={{ flex: span(p.start, p.warmup_end) / total }}>
          Warm-up
        </div>
        <div className="train" style={{ flex: span(p.train_start, p.train_end) / total }}>
          Train · thresholds tuned
          <span className="muted" style={{ fontSize: 11 }}>
            {day(p.train_start)} – {day(p.train_end)}
          </span>
        </div>
        <div className="test" style={{ flex: span(p.test_start, p.test_end) / total }}>
          Test · reported here
          <span style={{ fontSize: 11, opacity: 0.8 }}>
            {day(p.test_start)} – {day(p.test_end)}
          </span>
        </div>
      </div>
      <div className="table-wrap" style={{ maxWidth: 760 }}>
        <table className="data compact">
          <thead>
            <tr>
              <th>Synthetic event class</th>
              <th className="r">Train</th>
              <th className="r">Test</th>
            </tr>
          </thead>
          <tbody>
            {classes.map((c) => (
              <tr key={c}>
                <td>{TRUTH_LABELS[c]}</td>
                <td className="r num">{r.label_counts.train[c] ?? 0}</td>
                <td className="r num">{r.label_counts.test[c] ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 13 }}>
        The first eight weeks only build history. Events are assigned to the period containing their start date. Labels are read only by the
        evaluation module and never reach the detector.
      </p>
    </section>
  );
}

function MetricRow({
  name,
  note,
  t,
  pick,
  detail,
  scale = 1,
  format = (v: number) => pct(v),
  lowerBetter = false,
}: {
  name: string;
  note: string;
  t: Record<Method, PeriodMetrics>;
  pick: (m: PeriodMetrics) => [number | null, (number | null)[] | null];
  detail: (m: PeriodMetrics) => string;
  scale?: number;
  format?: (v: number) => string;
  lowerBetter?: boolean;
}) {
  return (
    <div className="metric-row">
      <div className="m-name">
        {name}
        <small>{note}</small>
      </div>
      <div className="mbars">
        {METHODS.map((k) => {
          const [v, interval] = pick(t[k]);
          const w = v === null ? 0 : Math.min(100, (v / scale) * 100);
          return (
            <div className={`mbar ${k}`} key={k}>
              <span className="label">{SHORT[k]}</span>
              <span className="track" aria-hidden="true">
                <span style={{ width: `${w}%` }} />
                {interval && interval[0] !== null && interval[1] !== null && (
                  <span className="ci" style={{ left: `${(interval[0] / scale) * 100}%`, width: `${((interval[1] - interval[0]) / scale) * 100}%` }} />
                )}
              </span>
              <span className="v">
                {v === null ? "n/a" : format(v)}
                <span className="sub muted" style={{ display: "block", fontSize: 11, fontWeight: 400 }}>
                  {detail(t[k])}
                </span>
              </span>
            </div>
          );
        })}
        {lowerBetter && <span className="sr-only">Lower values are better for this metric.</span>}
      </div>
    </div>
  );
}

function Breakdown({ t }: { t: Record<Method, PeriodMetrics> }) {
  return (
    <div className="table-wrap">
      <table className="data compact">
        <caption className="sr-only">False alerts and recall by class</caption>
        <thead>
          <tr>
            <th>Method</th>
            <th className="r">Recall: access gaps</th>
            <th className="r">Recall: reporting failures</th>
            <th className="r">Recall: ambiguous</th>
            <th className="r">False alerts on closures</th>
            <th className="r">on ordinary dips</th>
            <th className="r">with no event</th>
          </tr>
        </thead>
        <tbody>
          {METHODS.map((k) => {
            const d = t[k].detection;
            return (
              <tr key={k} className={k === "engine" ? "hl" : ""}>
                <td>{SHORT[k]}</td>
                <td className="r num">{pct(d.recall_by_class.ACCESS_GAP?.recall)}</td>
                <td className="r num">{pct(d.recall_by_class.REPORTING_FAILURE?.recall)}</td>
                <td className="r num">{pct(d.recall_by_class.AMBIGUOUS?.recall)}</td>
                <td className="r num">{d.false_alerts_by_truth.SCHEDULED_CLOSURE}</td>
                <td className="r num">{d.false_alerts_by_truth.NORMAL_VARIATION}</td>
                <td className="r num">{d.false_alerts_by_truth.NO_EVENT}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Subtypes({ m }: { m: PeriodMetrics }) {
  return (
    <section className="section" aria-labelledby="sub-h">
      <h2 id="sub-h">Outcome by scenario subtype (engine, test period)</h2>
      <div className="table-wrap">
        <table className="data compact">
          <thead>
            <tr>
              <th>Class</th>
              <th>Subtype</th>
              <th className="r">Events</th>
              <th className="r">Alerted</th>
              <th>Reported as</th>
            </tr>
          </thead>
          <tbody>
            {m.subtype_detail.map((s) => (
              <tr key={`${s.class}-${s.subtype}`}>
                <td>{TRUTH_LABELS[s.class]}</td>
                <td>{s.subtype.replace(/_/g, " ")}</td>
                <td className="r num">{s.events}</td>
                <td className="r num">{s.detected}</td>
                <td>
                  {Object.entries(s.predicted)
                    .map(([k, v]) => `${k === "NOT_FLAGGED" ? "not flagged" : CATEGORY_META[k as Category]?.short.toLowerCase() ?? k} ×${v}`)
                    .join(", ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 13 }}>
        “Covert” reporting failures leave no trace in feed metadata by construction, so no method can tell them apart from a real decline.
        They are included to show that limit honestly.
      </p>
    </section>
  );
}

function Tiers({ r }: { r: EvaluationReport }) {
  const eng = r.test.engine.volume_tiers ?? [];
  const nai = r.test.naive.volume_tiers ?? [];
  return (
    <section className="section" aria-labelledby="tier-h">
      <h2 id="tier-h">Performance by site volume</h2>
      <p className="prose">
        Streams are grouped by their median daily count in the training period (high ≥ 100, medium 30–99, low &lt; 30). Low-volume services
        are noisier, so modest declines are harder to detect — a fairness concern for small and rural services that the priority score does
        not compound (it is volume-neutral).
      </p>
      <div className="table-wrap">
        <table className="data compact">
          <thead>
            <tr>
              <th>Volume tier</th>
              <th className="r">Streams</th>
              <th className="r">Alertable events</th>
              <th className="r">Engine recall</th>
              <th className="r">Engine precision</th>
              <th className="r">Engine explanation accuracy</th>
              <th className="r">Naive recall</th>
              <th className="r">Naive precision</th>
              <th className="r">Naive false alerts /100 sw</th>
            </tr>
          </thead>
          <tbody>
            {eng.map((e) => {
              const n = nai.find((x) => x.tier === e.tier);
              return (
                <tr key={e.tier}>
                  <td style={{ textTransform: "capitalize" }}>{e.tier}</td>
                  <td className="r num">{e.streams.length}</td>
                  <td className="r num">{e.alertable_events}</td>
                  <td className="r num">{pct(e.recall)}</td>
                  <td className="r num">{pct(e.precision)}</td>
                  <td className="r num">{pct(e.explanation_accuracy)}</td>
                  <td className="r num">{pct(n?.recall)}</td>
                  <td className="r num">{pct(n?.precision)}</td>
                  <td className="r num">{num(n?.false_alerts_per_100_stream_weeks, 1)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Robustness({ r }: { r: EvaluationReport }) {
  const rows = r.robustness_missingness.map((x) => ({
    share: `${Math.round(x.missing_share * 100)}%`,
    ...Object.fromEntries(METHODS.flatMap((k) => [[`fa_${k}`, x.methods[k].false_service_gap_alerts_per_100_stream_weeks], [`prec_${k}`, x.methods[k].access_gap_precision]])),
  }));
  const chart = (key: "fa" | "prec", title: string, fmt: (v: number) => string, domain?: [number, number]) => (
    <figure className="panel panel-pad" style={{ margin: 0, display: "grid", gap: 8 }}>
      <figcaption>
        <h3>{title}</h3>
      </figcaption>
      <div className="legend">
        {METHODS.map((k) => (
          <span className="item" key={k}>
            <span className="ln" style={{ background: LINE[k] }} />
            {SHORT[k]}
          </span>
        ))}
      </div>
      <div style={{ width: "100%", height: 230 }} role="img" aria-label={title}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 14, right: 70, bottom: 4, left: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="share" tick={{ fontSize: 11, fill: "var(--muted)" }} tickLine={false} axisLine={{ stroke: "var(--axis)" }} />
            <YAxis width={44} tick={{ fontSize: 11, fill: "var(--muted)" }} tickLine={false} axisLine={false} tickFormatter={(v: number) => fmt(v)} domain={domain} />
            <Tooltip content={<RobustTip keyPrefix={key} fmt={fmt} />} isAnimationActive={false} />
            {METHODS.map((k) => (
              <Line key={k} dataKey={`${key}_${k}`} stroke={LINE[k]} strokeWidth={2} dot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)", fill: LINE[k] }} isAnimationActive={false} connectNulls>
                {k === "engine" && <LabelList dataKey={`${key}_${k}`} content={(p) => endLabel(p, rows.length, fmt)} />}
              </Line>
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
  return (
    <section className="section" aria-labelledby="rob-h">
      <h2 id="rob-h">Robustness to incomplete reporting</h2>
      <p className="prose">
        Rows were deleted at random from the test period (5–20%) and every method re-run with its tuned thresholds. The naive baseline reads
        a missing record as zero activity, so missing data turns directly into false service-gap alerts. The engine records those days as
        unknown and routes them to data quality instead.
      </p>
      <div className="grid-auto" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))" }}>
        {chart("fa", "False service-gap alerts per 100 stream-weeks", (v) => num(v, 1))}
        {chart("prec", "Precision of “access gap” alerts", (v) => pct(v), [0, 1])}
      </div>
      <p className="muted" style={{ fontSize: 13 }}>
        Each cell: event recall · false service-gap alerts per 100 stream-weeks · reporting-problem alerts raised. The engine’s recall rises
        with missingness because randomly missing days next to real events trigger reporting-problem alerts that overlap them — recall alone
        is not a good measure here, which is why false service-gap alerts are charted.
      </p>
      <div className="table-wrap">
        <table className="data compact">
          <caption className="sr-only">Robustness table</caption>
          <thead>
            <tr>
              <th>Rows removed</th>
              {METHODS.map((k) => (
                <th key={k} className="r">
                  {SHORT[k]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {r.robustness_missingness.map((x) => (
              <tr key={x.missing_share}>
                <td>{Math.round(x.missing_share * 100)}%</td>
                {METHODS.map((k) => (
                  <td key={k} className="r num">
                    {pct(x.methods[k].recall)} · {num(x.methods[k].false_service_gap_alerts_per_100_stream_weeks, 1)} · {x.methods[k].reporting_alerts}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function endLabel(p: unknown, n: number, fmt: (v: number) => string) {
  const props = p as { index?: number; x?: number; y?: number; value?: number };
  if (props.index !== n - 1 || props.value === undefined || props.value === null) return null;
  return (
    <text x={(props.x ?? 0) + 8} y={(props.y ?? 0) + 4} style={{ fontSize: 11, fill: "var(--ink-2)" }}>
      {fmt(props.value)} engine
    </text>
  );
}

function RobustTip({ active, payload, label, keyPrefix, fmt }: { active?: boolean; payload?: { dataKey: string; value: number }[]; label?: string; keyPrefix: string; fmt: (v: number) => string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tip">
      <div className="t-head">{label} of rows removed</div>
      {METHODS.map((k) => {
        const p = payload.find((x) => x.dataKey === `${keyPrefix}_${k}`);
        return (
          <div className="t-row" key={k}>
            <span className="k" style={{ background: LINE[k] }} />
            <span>{SHORT[k]}</span>
            <b>{p && p.value !== null && p.value !== undefined ? fmt(p.value) : "n/a"}</b>
          </div>
        );
      })}
    </div>
  );
}

function Replay({ r }: { r: EvaluationReport }) {
  if (!r.operational_replay.length) {
    return (
      <section className="section">
        <h2>Delayed reporting</h2>
        <p className="muted">The operational replay was skipped for this run (SILENCE_EVAL_REPLAY=0).</p>
      </section>
    );
  }
  return (
    <section className="section" aria-labelledby="rep-h">
      <h2 id="rep-h">Delayed reporting: an operational replay</h2>
      <p className="prose">
        The test period was replayed day by day using only the records that had <em>arrived</em> by each evening (about 14% of synthetic
        records arrive one to three days late). Assessing a day immediately is faster but raises transient alerts on data that is merely
        late; waiting one day trades a little speed for far fewer false starts.
      </p>
      <div className="table-wrap" style={{ maxWidth: 900 }}>
        <table className="data">
          <thead>
            <tr>
              <th>Grace period before assessing a day</th>
              <th className="r">Events alerted</th>
              <th className="r">Median delay</th>
              <th className="r">p90 delay</th>
              <th className="r">Correct category at first alert</th>
              <th className="r">Transient alerts</th>
            </tr>
          </thead>
          <tbody>
            {r.operational_replay.map((x) => (
              <tr key={x.grace_days}>
                <td>{x.grace_days === 0 ? "None (same evening)" : `${x.grace_days} day`}</td>
                <td className="r num">
                  {pct(x.recall)} ({x.detected}/{x.alertable_events})
                </td>
                <td className="r num">{num(x.operational_delay_days.median, 1)} d</td>
                <td className="r num">{num(x.operational_delay_days.p90, 1)} d</td>
                <td className="r num">{pct(x.category_correct_at_first_alert)}</td>
                <td className="r num">{x.transient_alerts}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 13 }}>
        Transient alerts are alerts raised during the replay that do not exist once all late records have arrived. Replay recall can exceed
        retrospective recall because a temporarily missing record can coincide with a real event.
      </p>
    </section>
  );
}

function Tuning({ r }: { r: EvaluationReport }) {
  const [open, setOpen] = useState(false);
  const grid = [...r.grid.engine].sort((a, b) => (b.f1 ?? 0) - (a.f1 ?? 0));
  return (
    <section className="section" aria-labelledby="tune-h">
      <h2 id="tune-h">Thresholds chosen on the training period</h2>
      <div className="table-wrap" style={{ maxWidth: 900 }}>
        <table className="data compact">
          <thead>
            <tr>
              <th>Method</th>
              <th>Selected parameters</th>
            </tr>
          </thead>
          <tbody>
            {METHODS.map((k) => (
              <tr key={k}>
                <td>{SHORT[k]}</td>
                <td className="mono">
                  {Object.entries(r.tuned_params[k])
                    .map(([a, b]) => `${a}=${b}`)
                    .join("  ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 13 }}>
        Each method’s grid was searched on training-period labels only (objective: event-level F1, then explanation accuracy, then fewer
        false alerts). The test period was never used for selection — an automated test checks that removing test labels does not change the
        chosen thresholds.
      </p>
      <div>
        <button className="btn small" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "Hide" : "Show"} the engine’s full training grid ({grid.length} settings)
        </button>
      </div>
      {open && (
        <div className="table-wrap" style={{ maxHeight: 380, overflowY: "auto", maxWidth: 900 }}>
          <table className="data compact">
            <thead>
              <tr>
                <th>z threshold</th>
                <th>Min shortfall</th>
                <th>Persistence</th>
                <th className="r">Train F1</th>
                <th className="r">Recall</th>
                <th className="r">Precision</th>
                <th className="r">Explanation accuracy</th>
              </tr>
            </thead>
            <tbody>
              {grid.map((g, i) => (
                <tr key={i}>
                  <td className="num">{g.params.z_threshold}</td>
                  <td className="num">{g.params.min_shortfall}</td>
                  <td className="num">{g.params.min_persistence}</td>
                  <td className="r num">{num(g.f1 === null ? null : g.f1 * 100, 1)}</td>
                  <td className="r num">{pct(g.recall)}</td>
                  <td className="r num">{pct(g.precision)}</td>
                  <td className="r num">{pct(g.explanation_accuracy ?? null)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="hash">Results hash {shortHash(r.results_sha256, 16)}</p>
    </section>
  );
}
