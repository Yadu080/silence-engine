import { useEffect, useState } from "react";
import { CategoryBadge, Empty, ErrorState, LoadingBlock, Notice } from "../components/Bits";
import { Confusion } from "../components/ConfusionMatrix";
import { IconRefresh } from "../components/Icons";
import { ApiError, api, requestHealthRefresh } from "../lib/api";
import { CATEGORY_META, TRUTH_LABELS, dateTime, day, pct, shortHash } from "../lib/format";
import { href, useApi } from "../lib/hooks";
import type { Category, Health, MlAccuracy, MlTriageReport, MlTriageResponse } from "../lib/types";

const ci = (v: (number | null)[]): string => (v && v[0] !== null && v[1] !== null ? `(${pct(v[0])}–${pct(v[1])})` : "");
const acc = (a: MlAccuracy): string => `${pct(a.accuracy)} (${a.correct}/${a.episodes})`;
const fix = (v: number, digits: number): string => v.toFixed(digits);
const catName = (c: string): string => CATEGORY_META[c as Category]?.short ?? c;

/** Optional ML triage benchmark. The rule-based triage stays the dashboard default whatever this shows. */
export function MlTriageSection({ health }: { health: Health }) {
  const ml = useApi<MlTriageResponse>("/api/ml-triage");
  const [runError, setRunError] = useState<string | null>(null);
  const job = health.job;
  const running = job?.status === "running";
  const jobKey = job ? `${job.job_id}:${job.status}` : "";

  // Reload when a background job (evaluation or ML run) changes state.
  useEffect(() => {
    if (jobKey) ml.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobKey]);

  async function run() {
    setRunError(null);
    try {
      await api.post("/api/ml-triage/run");
      requestHealthRefresh();
    } catch (e) {
      setRunError(e instanceof ApiError ? e.message : String(e));
    }
  }

  const head = (
    <div className="section-head">
      <div>
        <h2 id="ml-h">Optional: machine-learning triage vs the rules</h2>
        <p>
          A scikit-learn model, trained only on the synthetic training period, explains the same flagged episodes as the rule-based triage.
          The rules remain the dashboard default.
        </p>
      </div>
    </div>
  );

  if (ml.error) {
    return (
      <section className="section" aria-labelledby="ml-h">
        {head}
        <ErrorState error={ml.error} onRetry={ml.reload} />
      </section>
    );
  }
  if (!ml.data) {
    return (
      <section className="section" aria-labelledby="ml-h">
        {head}
        <LoadingBlock rows={3} label="Loading ML comparison" />
      </section>
    );
  }
  const d = ml.data;

  if (!d.available) {
    return (
      <section className="section" aria-labelledby="ml-h">
        {head}
        <Notice tone="info">
          <strong>Not available in this Python environment.</strong> {d.reason}
        </Notice>
        <p className="muted" style={{ fontSize: 13 }}>
          Nothing else on this page depends on it: detection, rule-based triage and every number above were computed with the Python standard
          library only. Installed ML packages:{" "}
          {Object.entries(d.packages)
            .filter(([, v]) => v)
            .map(([k, v]) => `${k} ${v}`)
            .join(", ") || "none"}
          .
        </p>
      </section>
    );
  }

  if (!d.report) {
    return (
      <section className="section" aria-labelledby="ml-h">
        {head}
        <Empty
          title="Not run for the loaded data yet"
          action={
            <button className="btn" onClick={run} disabled={running}>
              <IconRefresh size={15} /> {running ? "Running…" : "Run the ML comparison"}
            </button>
          }
        >
          {d.reason}
        </Empty>
        {runError && <Notice tone="error">{runError}</Notice>}
      </section>
    );
  }

  const r: MlTriageReport = d.report;
  const rules = r.test.rules;
  const mlm = r.test.ml;
  const p = r.test.paired;
  const onZ = r.runtime.machine === "s390x";
  const rows: { label: string; a: string; b: string }[] = [
    { label: "Explanation accuracy given detection", a: `${pct(rules.explanation.accuracy_given_detection)} (${rules.explanation.correct}/${rules.explanation.detected})`, b: `${pct(mlm.explanation.accuracy_given_detection)} (${mlm.explanation.correct}/${mlm.explanation.detected})` },
    { label: "  95% Wilson interval", a: ci(rules.explanation.accuracy_ci), b: ci(mlm.explanation.accuracy_ci) },
    { label: "Event recall", a: pct(rules.detection.recall), b: pct(mlm.detection.recall) },
    { label: "Alert precision", a: pct(rules.detection.precision), b: pct(mlm.detection.precision) },
    { label: "Reporting failures called access gaps", a: pct(rules.explanation.reporting_failures_called_access_gaps), b: pct(mlm.explanation.reporting_failures_called_access_gaps) },
    ...rules.explanation.per_class.map((pc, i) => {
      const q = mlm.explanation.per_class[i];
      return { label: `${TRUTH_LABELS[pc.truth_class]}: recall / precision`, a: `${pct(pc.recall)} / ${pct(pc.precision)}`, b: `${pct(q.recall)} / ${pct(q.precision)}` };
    }),
  ];

  return (
    <section className="section" aria-labelledby="ml-h">
      <div className="page-head" style={{ alignItems: "flex-start" }}>
        <div className="section-head" style={{ margin: 0 }}>
          <div>
            <h2 id="ml-h">Optional: machine-learning triage vs the rules</h2>
            <p>
              A scikit-learn {r.model.name.replace("Classifier", " classifier")}, trained only on the synthetic training period, explains the same
              flagged episodes as the rule-based triage. The rules remain the dashboard default.
            </p>
          </div>
        </div>
        <div style={{ display: "grid", gap: 6, justifyItems: "end" }}>
          <button className="btn" onClick={run} disabled={running}>
            <IconRefresh size={15} /> {running ? "Running…" : "Re-run ML comparison"}
          </button>
          <span className="muted" style={{ fontSize: 12 }}>
            Last run {dateTime(r.generated_at)} on {r.runtime.machine} · {r.duration_seconds}s
          </span>
        </div>
      </div>
      {runError && <Notice tone="error">{runError}</Notice>}
      {d.stale && (
        <Notice tone="warn">
          This comparison was computed for different data or detector thresholds than the ones now loaded. Re-run it before relying on it.
        </Notice>
      )}
      <Notice tone="info">
        <div className="ml-verdict">
          <strong>{r.conclusion.headline}</strong>
          <ul>
            {r.conclusion.points.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
          <span>
            Production classifier in this dashboard: <b>{r.production_classifier.name}</b>. {r.production_classifier.reason}
          </span>
        </div>
      </Notice>

      <div className="table-wrap" style={{ maxWidth: 900 }}>
        <table className="data">
          <caption className="sr-only">Rule-based triage and ML triage on the test period</caption>
          <thead>
            <tr>
              <th>Test period ({day(r.test.window[0])} – {day(r.test.window[1])})</th>
              <th className="r">Rule-based triage</th>
              <th className="r">ML (random forest)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((x) => (
              <tr key={x.label}>
                <td style={{ whiteSpace: "pre" }}>{x.label}</td>
                <td className="r num">{x.a}</td>
                <td className="r num">{x.b}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 13 }}>
        Both methods explain the episodes the same detector flagged, and both are scored by the same function with the same intervals. Per
        episode ({p.episodes}): both right {p.both_correct}, only the rules right {p.rules_only_correct}, only the ML model right{" "}
        {p.ml_only_correct}, both wrong {p.both_wrong}; exact McNemar p = {fix(p.mcnemar_exact_p, 2)}.
      </p>

      <div className="grid-auto" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(420px, 1fr))" }}>
        <Confusion title="Rule-based triage" m={rules} />
        <Confusion title="ML triage" m={mlm} />
      </div>

      <h3>How it was trained</h3>
      <dl className="kv">
        <dt>Training episodes</dt>
        <dd>
          {r.training.episodes} flagged episodes ({Object.entries(r.training.by_target).map(([k, v]) => `${catName(k).toLowerCase()} ${v}`).join(", ")}) from{" "}
          {day(r.training.window[0])} – {day(r.training.window[1])}. {r.training.built_from}
        </dd>
        <dt>Features</dt>
        <dd>
          {r.model.features.length} per episode, from the same evidence the rules read. {r.model.inputs_excluded}
        </dd>
        <dt>Settings</dt>
        <dd className="mono">{Object.entries(r.model.spec).map(([k, v]) => `${k}=${v}`).join("  ")} (fixed in advance, not tuned)</dd>
        <dt>Check on training data</dt>
        <dd>
          {r.training.cv_accuracy ? `${r.training.cv_accuracy.folds.length}-fold cross-validated accuracy ${pct(r.training.cv_accuracy.mean)}` : "too few episodes per class for cross-validation"};
          in-sample accuracy {pct(r.training.in_sample_accuracy)} ({r.training.in_sample_note.toLowerCase()})
        </dd>
        <dt>Detector thresholds</dt>
        <dd className="mono">
          z_threshold={String(r.detector.params.z_threshold)} min_shortfall={String(r.detector.params.min_shortfall)} min_persistence={String(r.detector.params.min_persistence)}
        </dd>
      </dl>

      <Importances r={r} />
      <Sensitivity r={r} />
      <Disagreements r={r} />

      <h3>Reproducibility</h3>
      <dl className="kv">
        <dt>Computed on</dt>
        <dd>
          {r.runtime.machine} ({r.runtime.byteorder}-endian), Python {r.runtime.python}
          {Object.keys(r.runtime.libraries).length ? `; ${Object.entries(r.runtime.libraries).map(([k, v]) => `${k} ${v}`).join(", ")}` : ""}.{" "}
          {onZ ? "This run was on IBM Z / LinuxONE (s390x)." : "Not run on s390x by this report; compare the hashes from a LinuxONE run before claiming that."}
        </dd>
        <dt>Results hash</dt>
        <dd className="hash">{r.results_sha256}</dd>
        <dt>Predictions hash</dt>
        <dd className="hash">{r.predictions_sha256}</dd>
      </dl>
      <p className="muted" style={{ fontSize: 13 }}>
        The same seed gives the same hashes on one machine and library version. The predictions hash covers only the per-episode predicted
        categories, so it is the more robust comparison across machines. Reproduce with <code>python manage.py evaluate-ml</code>.
      </p>
    </section>
  );
}

function Importances({ r }: { r: MlTriageReport }) {
  const [all, setAll] = useState(false);
  const rows = all ? r.importances : r.importances.slice(0, 8);
  const maxPerm = Math.max(0.001, ...r.importances.map((i) => Math.max(0, i.permutation_mean)));
  const maxImp = Math.max(0.001, ...r.importances.map((i) => i.impurity));
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <h3>Which signals the model used</h3>
      <p className="prose">
        Built-in importance is the model’s own split-based measure on the training data. Permutation importance is the accuracy lost on the test
        period when one feature is shuffled; it can be zero for a feature that is correlated with another. Both tables describe the model, not
        the world.
      </p>
      <div className="table-wrap">
        <table className="data compact">
          <thead>
            <tr>
              <th>Feature</th>
              <th>Built-in importance</th>
              <th>Permutation importance (± sd)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((i) => (
              <tr key={i.feature}>
                <td title={i.description}>
                  <span className="mono">{i.feature}</span>
                  <small className="muted" style={{ display: "block" }}>{i.description}</small>
                </td>
                <td>
                  <span className="mini-bar" aria-hidden="true"><span style={{ width: `${(i.impurity / maxImp) * 100}%` }} /></span> <span className="num">{fix(i.impurity, 3)}</span>
                </td>
                <td>
                  <span className="mini-bar" aria-hidden="true"><span style={{ width: `${(Math.max(0, i.permutation_mean) / maxPerm) * 100}%` }} /></span>{" "}
                  <span className="num">{i.permutation_mean >= 0 ? "+" : ""}{fix(i.permutation_mean, 3)} ± {fix(i.permutation_std, 3)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <button className="btn small" onClick={() => setAll(!all)} aria-expanded={all}>
          {all ? "Show the top 8 only" : `Show all ${r.importances.length} features`}
        </button>
      </div>
    </div>
  );
}

function Sensitivity({ r }: { r: MlTriageReport }) {
  const s = r.sensitivity;
  const n = r.test.episodes;
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <h3>Is the result stable, and does anything leak?</h3>
      <div className="table-wrap" style={{ maxWidth: 900 }}>
        <table className="data compact">
          <thead>
            <tr>
              <th>Check</th>
              <th className="r">Test episodes right (of {n})</th>
              <th>What it shows</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Headline model</td>
              <td className="r num">{acc(r.test.paired.ml)}</td>
              <td>Fixed random seed.</td>
            </tr>
            <tr>
              <td>Other random seeds</td>
              <td className="r num">{Math.min(...s.other_seeds.correct)}–{Math.max(...s.other_seeds.correct)}</td>
              <td>{s.other_seeds.note}</td>
            </tr>
            <tr>
              <td>Gradient boosting</td>
              <td className="r num">{acc(s.gradient_boosting)}</td>
              <td>{s.gradient_boosting.note}</td>
            </tr>
            <tr>
              <td>Always the most common class</td>
              <td className="r num">{acc(s.majority_class)}</td>
              <td>{s.majority_class.note}</td>
            </tr>
            <tr>
              <td>Trained on shuffled targets</td>
              <td className="r num">{Math.min(...s.shuffled_label_control.correct)}–{Math.max(...s.shuffled_label_control.correct)}</td>
              <td>{s.shuffled_label_control.note}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Disagreements({ r }: { r: MlTriageReport }) {
  if (!r.disagreements.length) {
    return <p className="muted">The two methods gave the same category for every test episode.</p>;
  }
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <h3>Where they disagree, and why</h3>
      <p className="prose">
        Every test episode where the categories differ. “Why (rules)” is the rule that fired. “Why (ML)” lists the features whose replacement by a
        typical value lowers the model’s confidence in its answer most (an occlusion test), which is a diagnostic of this model, not an explanation of the world.
      </p>
      <div className="table-wrap">
        <table className="data compact">
          <thead>
            <tr>
              <th>Episode</th>
              <th>Synthetic truth</th>
              <th>Rules said</th>
              <th>ML said</th>
              <th>Right</th>
            </tr>
          </thead>
          <tbody>
            {r.disagreements.map((x) => (
              <tr key={x.case_id}>
                <td>
                  <a href={href(`/cases/${x.case_id}`)}>{x.case_id}</a>
                  <small className="muted" style={{ display: "block" }}>{x.site_name} · {day(x.onset)} – {day(x.end)} · feed {x.feed_status.toLowerCase()}</small>
                </td>
                <td>
                  {TRUTH_LABELS[x.truth_class] ?? x.truth_class}
                  {x.truth_subtype ? <small className="muted" style={{ display: "block" }}>{x.truth_subtype.replace(/_/g, " ")}</small> : null}
                </td>
                <td>
                  <CategoryBadge category={x.rule_category} short />
                  <small className="muted" style={{ display: "block", maxWidth: 280 }}>{x.rule_reason}</small>
                </td>
                <td>
                  <CategoryBadge category={x.ml_category} short />
                  <small className="muted" style={{ display: "block", maxWidth: 280 }}>
                    {Object.entries(x.ml_probabilities).map(([k, v]) => `${catName(k).toLowerCase()} ${pct(v)}`).join(" · ")}
                    {x.ml_top_features.length ? ` — leaned on ${x.ml_top_features.map((f) => `${f.feature} = ${fix(f.value, 2)}`).join(", ")}` : ""}
                  </small>
                </td>
                <td>{x.correct === "rules" ? "Rules" : x.correct === "ml" ? "ML" : "Neither"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hash">Predictions hash {shortHash(r.predictions_sha256, 16)}</p>
    </div>
  );
}
