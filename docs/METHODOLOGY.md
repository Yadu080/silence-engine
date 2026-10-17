# Methodology

SILENCE ENGINE turns aggregate daily service records into **investigation hypotheses**. Every step is deterministic, interpretable and testable without the web interface (`backend/silence_engine/`). Nothing here estimates causal effects. Sections 0–8 describe the production pipeline (Python standard library only). Section 9 describes an **optional** machine-learning triage model that is benchmarked against it and is never the default.

## 0. Unit of analysis

A **stream** is one service type at one location (`site_id:service_type`), observed once per calendar day. Records are aggregate counts — no personal data.

Each stream-day ends up with one of these statuses:

| Status | Meaning |
|---|---|
| `closed` | Schedule says closed on a normally-closed weekday |
| `closed_unusual` | Schedule says closed on a weekday that is normally open (e.g. holiday, maintenance) |
| `ok` | Trusted count within the expected range |
| `low` | Trusted count well below expected (flagged) |
| `missing` | Open day with **no count** (no record, blank value, or malformed value) |
| `suspect` | A count arrived but feed health casts doubt on it |
| `no_baseline` | Not enough comparable history to judge |

**Missing is never converted to zero.** A blank `observed_count` is stored as `NULL`; a malformed value (e.g. `"12O"`, `-3`) is stored as `NULL` with `value_status = 'malformed'`; the literal `0` is a real observation. A day with no record at all is materialised as `missing_record`; its schedule is inferred from the majority of the same weekday over the previous 8 weeks and marked `schedule_inferred`.

## 1. Feed health (independent of activity levels) — `data_quality.py`

Computed per stream-day before any baseline:

| Check | Rule |
|---|---|
| Missing record / value | Open day without a record, or record with blank count |
| Malformed value | Non-integer, negative, or > 1,000,000 |
| Duplicates | Several rows for one stream-day → keep the most recently received; flag `duplicate_conflict` if the counts differ |
| Heartbeat | `feed_heartbeat_received = false` on an open day |
| Staleness | End of service day − `feed_last_seen` > 1 day |
| Incomplete uploads | `batches_received / batches_expected` < 0.75 |
| Zero-fill | Count is 0 while the heartbeat is missing or zero batches arrived (placeholder zero) |
| Flatline | Run of identical non-zero counts of length ≥ L, where L is the smallest run that Poisson noise at the stream's trailing level produces with probability < 10⁻⁴ (`stats.flatline_run_length`; e.g. L = 4 at ~200/day, L = 6 at ~7/day). Zeros are excluded because a run of zeros may be a genuine closure. |
| Schedule consistency | Activity > max(5, 25% of typical) on a scheduled-closed day |
| Late arrival | Received more than 6 h after the end of the service day |

A count is **trusted** if the day is scheduled open, the count exists, and none of heartbeat/stale/incomplete/zero-fill/flatline apply.

**Causality.** All checks use only data up to the day being checked. The flatline flag is applied retroactively to the whole run once the run becomes long enough, so each flagged day records `distrust_known_on` (the day the run reached L). The baseline for day *d* excludes a past day only if its distrust was knowable before *d* (`DayRecord.trusted_as_of`).

## 2. Expected activity — `detection.compute_baselines`

For stream-day *d*:

- **History** = counts on the same weekday in the previous `lookback_weeks = 8` weeks, keeping only days that were scheduled open and trusted as known at the time.
- If fewer than `min_history = 4` values → `expected = None` (`no_baseline`); no confident assessment is made.
- **Expected** `E = median(history)`.
- **Spread** `σ = max(1.4826·MAD(history), √max(E, 1), σ_cv, 1)`, where `σ_cv = 1.4826·MAD(r)·E` and `r` are the last 42 trusted observed/expected ratios in [0.5, 2] (a pooled dispersion estimate that stabilises σ when only 4–8 same-weekday values exist). The Poisson floor prevents over-confidence at low volume.
- Closed days have expected 0 (`typical_if_open` keeps the usual open-day median for display).

Data from day *d* or later never enters *d*'s baseline. `tests/test_detection_triage.py::test_no_future_leakage_in_expected_values` alters all data after a cut-off and checks that every expected value, σ, z and status up to the cut-off is unchanged.

## 3. Measuring the gap — `detection.assess_stream`

For trusted open days with a baseline:

- absolute shortfall `S = E − O`
- percentage shortfall `S/E`, only when `E ≥ 1` (otherwise not reported)
- robust deviation `z = (O − E)/σ`
- one-sided lower CUSUM `C_d = min(1.5h, max(0, C_{d−1} − z_d − k))` with `k = 0.5`, `h = 4` (capped so one extreme day cannot hold the alarm long after recovery)

A day is `low` when either

- `z ≤ −z_threshold` **and** `S/E ≥ min_shortfall` (sharp drop), or
- `C_d ≥ h` **and** `S/E ≥ min_shortfall / 2` (persistent moderate decline).

`z_threshold`, `min_shortfall` and `min_persistence` are tuned on the training period (section 7). Shipped defaults: 3.0 / 0.25 / 2; tuned for seed 42: 2.5 / 0.35 / 2.

## 4. Episodes — `detection.find_episodes`

- A **silence episode** is a run of open days with status `low`, `missing` or `suspect`. Closed days bridge a run; one ordinary day inside a run is tolerated (`gap_tolerance = 1`).
- An episode becomes an **alert** when it reaches `min_persistence` non-ok open days; the **alert date** is that day (retrospective analysis assumes data arrives on the service day; the operational replay in section 8 removes that assumption).
- **Onset** is the first flagged day, or the CUSUM change-point estimate (the day the CUSUM statistic last left zero) if earlier.
- A **closure episode** is a run of `closed_unusual` days; it is always triaged as expected variation and is not an alert.

## 5. Triage — `triage.py`

Features for the episode window (open days only):

- **Trusted decline**: on days with trusted counts, `frac = 1 − ΣO/ΣE` and aggregate `z_agg = (ΣO − ΣE)/√Σσ²`. Strength: **STRONG** if `frac ≥ 0.35` and `z_agg ≤ −4`; **MODERATE** if `frac ≥ 0.20` and `z_agg ≤ −2.5`; WEAK/NONE otherwise; UNMEASURABLE if no trusted days.
- **Feed status**: share of open days that are `missing` or `suspect`. **FAILED** ≥ 0.6, **DEGRADED** ≥ 0.2, else **HEALTHY**.
- **Context** over the window ±1 day: severe weather (index ≥ 2), transport disruption, reported outage. Context is reported as *coinciding*, never as a cause.
- **Peers**: other streams at the same site or in the same region; how many show a ≥ 20% trusted shortfall over the same days.
- **Historical precedent**: over the previous 120 days of ordinary (`ok`) days, the share of windows of the same length (≤ 7) whose mean shortfall was at least this large.
- **History adequacy**: median number of comparable past days.

Decision rules (fixed in advance, not tuned):

```
closure episode                               → EXPECTED_VARIATION (scheduled_closure)
feed FAILED:   strong decline on ≥2 trusted days → UNCERTAIN (decline_with_failed_feed)
               otherwise                       → REPORTING_PROBLEM (feed_failure)
feed DEGRADED: strong or moderate decline      → UNCERTAIN (decline_with_degraded_feed)
               too little history              → UNCERTAIN (insufficient_history)
               otherwise                       → REPORTING_PROBLEM (degraded_feed)
feed HEALTHY:  too little history              → UNCERTAIN (insufficient_history)
               strong decline                  → POTENTIAL_ACCESS_GAP
               moderate decline, precedent ≥10% → EXPECTED_VARIATION (historical_precedent)
               moderate decline with context   → POTENTIAL_ACCESS_GAP
               moderate decline, no context    → UNCERTAIN
               weak / none                     → EXPECTED_VARIATION (within_normal_range)
```

Each evidence item records which hypotheses (ACCESS, REPORTING, NORMAL) it supports or contradicts and its strength. The case page shows this as a competing-hypotheses matrix with a verdict per hypothesis (Supported / Partly supported / Contested / Not supported / Cannot assess), plus limitations and category-specific verification steps.

## 6. Scores (heuristics, not probabilities)

**Investigation priority** (0–100), used only to order the queue:

```
priority = 100 × w_category × (0.5·magnitude + 0.3·persistence + 0.2·recency)
magnitude   = min(1, (unaccounted / expected_total) / 0.8)   unaccounted = trusted shortfall + expected on missing/suspect days
persistence = min(1, non-ok open days / 7)
recency     = 1 if ongoing, else exp(−days_since_end / 14)
w           = access gap 1.0, uncertain 0.85, reporting 0.7, expected 0.15
```

Priority is **volume-neutral** (relative magnitude, not absolute counts) so small rural services are not systematically pushed down the queue.

**Evidence sufficiency** (0–100):

```
100 × (0.25·history + 0.25·signal + 0.2·feed_clarity + 0.15·consistency + 0.15·persistence)
history      = min(1, comparable days / 8)
signal       = min(1, |z_agg| / 8) for decline-based categories; share of explicitly failed days for reporting
feed_clarity = healthy 1.0, failed 0.9, degraded 0.35
consistency  = share of open days matching the assigned interpretation
persistence  = min(1, non-ok days / 5)
```

Bands: ≥ 70 Strong, 45–69 Moderate, < 45 Limited. These are **not calibrated probabilities**. The evaluation reports triage accuracy by band on the synthetic test set as an empirical check (seed 42: Strong 35/40 correct, Moderate 6/7).

## 7. Evaluation protocol — `evaluation.py`

- **Labels** come from a separate JSON file written by the generator; only `evaluation.py` reads them.
- **Periods**: first 8 weeks warm-up (history only); next 45% of weeks = **train**; remainder = **test**. Events belong to the period containing their start date. Seed 42: train 1 Dec 2025 – 19 Apr 2026, test 20 Apr – 4 Oct 2026.
- **Methods**
  - *Naive % drop*: daily totals (duplicates summed, missing read as 0) vs the median of the previous 4 same weekdays; flag drops ≥ threshold for ≥ persistence days; every alert is called a service decline.
  - *Detector only (ablation)*: the engine's baselines, z, CUSUM and schedule awareness, but no feed-health checks (missing read as 0, all counts trusted) and no triage — every alert is called an access gap.
  - *SILENCE ENGINE*: full pipeline.
- **Tuning**: grid search on the **training period only** — engine and ablation over `z_threshold ∈ {2.5, 3, 3.5}`, `min_shortfall ∈ {0.15, 0.25, 0.35}`, `min_persistence ∈ {1, 2, 3}`; naive over drop ∈ {0.3…0.7}, persistence ∈ {1, 2, 3}. Objective: event-level F1, then explanation accuracy, then fewer false alerts. A test (`test_tuning_ignores_test_period_labels`) confirms that deleting all test-period labels does not change the selected thresholds.
- **Matching**: a prediction matches an event if it is on the same stream and its window overlaps the event window extended by ±1 day. Each event takes the earliest actionable overlapping alert; each alert is attributed to the event it overlaps most.
- **Detection metrics** (alertable = access gap, reporting failure, ambiguous): event recall, alert precision, F1, false alerts per 100 stream-weeks (split by closures, ordinary dips and no-event periods), benign events left alone, detection delay (alert date − event start).
- **Explanation metrics** (separate from detection): accuracy among detected events, confusion matrix (true class × reported category, including "not flagged"), per-category precision/recall with Wilson 95% intervals, share of reporting failures called access gaps, abstention rate.
- **Robustness**: 5/10/20% of test-period records deleted (order-independent hash selection); all methods re-run with their tuned thresholds.
- **Volume tiers**: streams grouped by median trusted daily count in the training period (high ≥ 100, medium 30–99, low < 30).
- **Reproducibility**: `results_sha256` hashes the metric content; the same seed gives the same hash on any machine (verified on CPython 3.11, 3.13 and 3.14 on macOS; compare on LinuxONE with `manage.py fingerprint`).

## 8. Delayed reporting — operational replay

The test period is replayed day by day. On each evening *T* the engine sees only records whose `report_received_at ≤ T 23:59`, assesses days up to `T − grace`, and the first evening an alert overlaps each event is recorded. Baselines use history at least 7 days old, which is complete because synthetic delays are at most 3 days. Seed 42: with no grace period, median operational delay 2 days and 20 transient alerts (raised on data that was merely late); with a 1-day grace period, median 3 days and 4 transient alerts.

## 9. Optional ML triage benchmark — `ml_triage.py`, `ml_evaluation.py`

Triage (section 5) maps a flagged episode to one of four hypotheses with hand-written thresholds. As a check on those hand-written rules, and to run real machine-learning code on LinuxONE, an optional scikit-learn model learns the same mapping from synthetic examples and is scored against the rules. **It never changes what the detector flags, the cases in the queue, their scores, or any number in sections 0–8.** The dashboard keeps the rule-based triage as the default, and the module is imported lazily so the engine, API and tests run without numpy or scikit-learn (PyPI has no s390x wheels for them; on LinuxONE use the ML container image's Python — see `IBM_Z_DEPLOYMENT.md`).

**Unit and scope.** The detector (thresholds tuned on the training period, section 7) flags silence episodes. Both triage methods then explain *the same* flagged episodes, so any difference comes from triage alone. Scheduled-closure episodes are decided by the schedule in both methods; they are a rule, not a learning problem.

**Features** (19 per episode, `ml_triage.FEATURES`), all read from the evidence the rule triage already computes: trusted shortfall fraction and aggregate z (0 if unmeasurable) with a has-trusted-days flag; the share of open days that are missing or suspect, missing, or flagged low; the share of open days carrying each feed flag (flatline, placeholder zero, no heartbeat, stale feed, incomplete batches); three context flags (severe weather, transport disruption, outage); the number of comparable services also declining and the number compared; the median history length behind the baseline; mean expected daily volume; episode duration. **The rule-based verdict (category, reason code, priority, evidence score) is never an input**; `tests/test_ml_triage.py` records which fields the feature function reads and fails if it touches anything outside the allowed list.

**Target.** The triage category matching the synthetic event the episode overlaps most: access gap → `POTENTIAL_ACCESS_GAP`, reporting failure → `REPORTING_PROBLEM`, ambiguous → `UNCERTAIN`; an episode overlapping no alertable event would map to `EXPECTED_VARIATION` (none occurred in the seed-42 training period).

**Training data and leakage controls.**
- The training set comes from a pipeline run over records dated **up to the end of the training period only**, episodes whose alert date falls in it, and labels of events that **start** in it (42 episodes for seed 42: access gap 16, reporting problem 15, uncertain 11). No test-period record or label can reach the model; `tests/test_ml_triage.py` destroys every later record and scrambles every later label and checks that the training matrix is unchanged, and deletes all test labels and checks that the predictions are unchanged.
- `ml_triage.py` (features, model wrapper, availability) is label-free and imports neither the evaluation harness nor the generator; `ml_evaluation.py` is evaluation-style code that receives labels as an argument. `tests/test_leakage.py` enforces both, that no detector module imports either, that numpy/scikit-learn are imported only lazily and only by these two modules, and that `requirements.txt` stays free of them.
- Hyper-parameters are fixed in advance and not tuned on any test result: random forest, 300 trees, depth ≤ 4, ≥ 2 samples per leaf, balanced class weights, seed 42.

**Evaluation.** The test period is scored by the very same `evaluation.score` function as the rules, with the same Wilson intervals, per-class precision and recall and confusion matrices; a test checks that the rule-based side of the comparison equals the main evaluation exactly. A paired episode-level comparison (both right, only rules right, only ML right, both wrong) comes with an exact McNemar test. Diagnostics: built-in (impurity) and permutation importances (accuracy lost on the test period when a feature is shuffled, 20 repeats); the same model with ten other random seeds; gradient boosting as a sensitivity check; always predicting the most common class; a model trained on randomly shuffled targets (a leakage control: near-chance accuracy means the features carry no label information); stratified 5-fold cross-validation on the training episodes only; and an occlusion explanation for every episode on which the two methods disagree.

**Reproducibility.** `results_sha256` hashes the metric content (not timings, hostnames or library versions); `predictions_sha256` hashes only the per-episode predicted categories and is the more robust cross-machine check. Fixed seeds, one thread. Seed 42 gives `results_sha256 = cbea084d…` and `predictions_sha256 = 6949d3d6…` on macOS arm64 with CPython 3.13.9 (scikit-learn 1.7.2, NumPy 2.3.5) **and** with CPython 3.11.4 (scikit-learn 1.5.1, NumPy 1.26.4). It has **not yet been run on s390x**; compare the hashes from the notebook's Part C there.

**Results on the synthetic test period (seed 42; 50 flagged episodes, 55 alertable events):**

| | Rule-based triage | ML (random forest) |
|---|---|---|
| Explanation accuracy given detection | 87% (41/47), 95% CI 75–94% | 88% (42/48), 95% CI 75–94% |
| Event recall / alert precision | 85% / 100% | 87% / 100% |
| Reporting failures called access gaps | 0% | 5% (1 of 22) |
| Access gap recall / precision | 73% / 100% | 77% / 95% |
| Reporting problem recall / precision | 95% / 84% | 91% / 87% |
| Uncertain recall / precision | 36% / 71% | 45% / 75% |

Per episode: both right 42, only the rules right 1, only the ML model right 2, both wrong 5 (exact McNemar p = 1.00). **There is no detectable difference.** The model's one-event lead comes from two episodes it got right and the rules did not: an access gap the rules passed as within normal range, and an ambiguous event the rules called a reporting problem. The rules were right on a feed dropout that the model called uncertain. Both methods get the same covert reporting failure wrong (the rules say uncertain, the model says access gap). The four disagreements are listed with the rule that fired and the features the model leaned on on the Evaluation page and in `manage.py evaluate-ml`.

Sensitivity (test episodes right out of 50): other seeds 44–45; gradient boosting 46; always the most common class 19; shuffled-target control 8–29 (mean 36%); cross-validated accuracy on the training episodes 88%. Permutation importance ranks the share of missing or suspect days first (about 4 points of accuracy when shuffled), then the heartbeat flag, the share of days flagged low and the aggregate z-score; built-in importance also puts the missing/suspect share first, followed by the z-score, the has-trusted-days flag and the incomplete-batches share. Those are the signals the hand-written rules threshold (feed-failure share and decline strength), and most other features show no measurable importance. In other words the model largely rediscovers the rules, and with 42 training episodes a single episode moves its accuracy by two points. **This benchmark gives no evidence that ML triage is better, so the rules remain the production classifier.** What it does show is that the hand-written thresholds are not leaving easy accuracy on the table on this data, and that the same evidence supports a learned model that runs on the LinuxONE ML stack.
