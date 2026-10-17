export type Category = "POTENTIAL_ACCESS_GAP" | "REPORTING_PROBLEM" | "UNCERTAIN" | "EXPECTED_VARIATION";
export type FeedStatus = "HEALTHY" | "DEGRADED" | "FAILED";
export type DayStatus = "closed" | "closed_unusual" | "ok" | "low" | "missing" | "suspect" | "no_baseline";

export interface Job {
  job_id: string;
  kind: string;
  status: "running" | "succeeded" | "failed";
  message: string;
  started_at: string;
  finished_at: string | null;
  error: { code: string; message: string } | null;
}

export interface RuntimeBrief {
  system: string;
  machine: string;
  python: string;
  byteorder: string;
  hostname: string;
}

export interface Health {
  status: string;
  engine_version: string;
  database: string;
  data_loaded: boolean;
  data_mode: string | null;
  synthetic_active: boolean;
  analysis_ready: boolean;
  analysis_finished_at: string | null;
  as_of: string | null;
  evaluation_ready: boolean;
  job: Job | null;
  runtime: RuntimeBrief;
}

export interface ContextFlags {
  severe_weather_days: number;
  weather_max: number | null;
  transport_days: number;
  outage_days: number;
}

export interface CaseBrief {
  case_id: string;
  stream_id: string;
  site_id: string;
  site_name: string;
  region: string | null;
  service_type: string;
  kind: "silence" | "closure";
  category: Category;
  category_label: string;
  reason_code: string;
  actionable: boolean;
  onset: string;
  end: string;
  alert_date: string | null;
  ongoing: boolean;
  duration_days: number;
  open_days: number;
  expected_total: number;
  reported_total: number;
  trusted_expected: number;
  trusted_observed: number;
  shortfall_frac: number | null;
  unaccounted_total: number;
  missing_expected: number;
  feed_status: FeedStatus;
  priority: number;
  priority_band: string;
  evidence_score: number;
  evidence_band: string;
  headline?: string;
  z_agg: number | null;
  context_flags: ContextFlags;
  review_status: string | null;
}

export interface Evidence {
  text: string;
  kind: string;
  supports: string[];
  contradicts: string[];
  strength: "strong" | "moderate" | "weak";
}

export interface Hypothesis {
  key: string;
  label: string;
  verdict: string;
  supporting: string[];
  contradicting: string[];
}

export interface Peer {
  stream_id: string;
  site_name: string;
  service_type: string;
  same_site: boolean;
  shortfall_frac: number;
  declining: boolean;
  days_compared: number;
}

export interface CaseFull extends Omit<CaseBrief, "context_flags" | "review_status"> {
  start: string;
  explanation: string;
  evidence: Evidence[];
  supporting: Evidence[];
  conflicting: Evidence[];
  hypotheses: Hypothesis[];
  limitations: string[];
  verification_steps: string[];
  flag_days: Record<string, number>;
  status_counts: Record<string, number>;
  history_median: number;
  mean_expected: number | null;
  min_z: number | null;
  feed_problem_frac: number;
  nonok_days: number;
  precedent_rate: number | null;
  precedent_windows: number;
  context: {
    window_start: string;
    window_end: string;
    weather_max: number | null;
    severe_weather_days: number;
    transport_days: number;
    outage_days: number;
  };
  peers: { compared: number; declining: number; peers: Peer[] };
  priority_components: Record<string, number | string>;
  evidence_components: Record<string, number | string>;
}

export interface DayView {
  date: string;
  observed: number | null;
  expected: number | null;
  band_low: number | null;
  band_high: number | null;
  typical_if_open: number | null;
  status: DayStatus;
  value_status: string;
  scheduled_open: boolean;
  schedule_inferred: boolean;
  trusted: boolean;
  z: number | null;
  shortfall_frac: number | null;
  n_history: number;
  flags: string[];
  weather: number | null;
  transport: boolean;
  outage: boolean;
  heartbeat: boolean | null;
  batch_completeness: number | null;
  stale_days: number | null;
  case_id: string | null;
  cusum: number | null;
}

export interface AggregateDay {
  date: string;
  expected: number;
  observed: number;
  unknown: number;
  streams_reporting: number;
  streams_unknown: number;
  case_streams: number;
}

export interface Review {
  id: number;
  case_id: string;
  status: string;
  note: string | null;
  reviewer_role: string | null;
  created_at: string;
}

export interface AuditEntry {
  id: number;
  ts: string;
  actor: string;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  details: Record<string, unknown>;
  prev_hash: string;
  hash: string;
}

export interface CaseDetail {
  case: CaseFull;
  timeline: DayView[];
  reviews: Review[];
  audit: AuditEntry[];
  related: CaseBrief[];
  review_statuses: string[];
}

export interface Summary {
  as_of: string;
  window_days: number;
  window_start: string;
  sites_monitored: number;
  streams_monitored: number;
  investigations_requiring_attention: number;
  recent_actionable_cases: number;
  recent_by_category: Record<Category, number>;
  all_time_by_category: Record<Category, number>;
  potential_access_gaps: number;
  reporting_problems: number;
  uncertain: number;
  feeds_not_healthy: number;
  feeds_not_healthy_list: { stream_id: string; status: string }[];
  activity_change_last7: number | null;
  activity_change_prev7: number | null;
  movers: { stream_id: string; change: number | null; unknown_days: number }[];
  ledger: {
    expected_total: number;
    trusted_observed: number;
    trusted_expected: number;
    unknown_due_to_feed: number;
    unaccounted_in_cases: number;
    by_category: Record<Category, number>;
    outside_cases_shortfall: number;
  };
  top_cases: CaseBrief[];
}

export interface StreamInfo {
  stream_id: string;
  site_id: string;
  service_type: string;
  site_name: string;
  region: string | null;
  synthetic: boolean;
  first_date: string;
  last_date: string;
  typical_daily: number | null;
}

export interface CaseList {
  total: number;
  offset: number;
  limit: number;
  category_totals: Record<Category, number>;
  items: CaseBrief[];
}

export type SilenceCell = [number, number | null, number] | null;

export interface SilenceMapData {
  dates: string[];
  status_codes: Record<DayStatus, number>;
  streams: { stream_id: string; site_name: string; service_type: string; region: string | null; cells: SilenceCell[] }[];
  cases: { case_id: string; category: Category; onset: string; end: string }[];
}

export interface StreamHealth {
  stream_id: string;
  site_id: string;
  service_type: string;
  site_name: string;
  region: string | null;
  status: FeedStatus;
  first_date: string;
  last_record_date: string | null;
  last_trusted_date: string | null;
  days_since_trusted: number | null;
  open_days_recent: number;
  completeness_total: number | null;
  completeness_recent: number | null;
  trusted_share_recent: number | null;
  problem_days_recent: number;
  counts_total: Record<string, number>;
  counts_recent: Record<string, number>;
  duplicate_rows_total: number;
  late_share: number | null;
}

export interface DqIssue {
  issue_id: string;
  stream_id: string;
  site_id: string;
  service_type: string;
  type: string;
  label: string;
  severity: "high" | "medium" | "low";
  start: string;
  end: string;
  days: number;
}

export interface DataQuality {
  as_of: string;
  streams: StreamHealth[];
  issues: DqIssue[];
  issue_day_totals: Record<string, number>;
  feed_matrix: { dates: string[]; streams: { stream_id: string; cells: (string | null)[] }[] };
  note: string;
}

export interface Summ {
  n: number;
  median: number | null;
  mean: number | null;
  p90: number | null;
  min: number | null;
  max: number | null;
}

export interface PeriodMetrics {
  detection: {
    alertable_events: number;
    detected: number;
    recall: number | null;
    recall_ci: (number | null)[];
    alerts: number;
    true_alerts: number;
    precision: number | null;
    precision_ci: (number | null)[];
    f1: number | null;
    false_alerts: number;
    false_alerts_per_100_stream_weeks: number | null;
    false_alerts_by_truth: Record<string, number>;
    false_service_gap_alerts: number;
    false_service_gap_alerts_per_100_stream_weeks: number | null;
    benign_events: number;
    benign_events_not_alerted: number;
    benign_handled_rate: number | null;
    delay_days: Summ;
    delay_by_class: Record<string, Summ>;
    recall_by_class: Record<string, { events: number; detected: number; recall: number | null }>;
    stream_weeks: number;
  };
  explanation: {
    accuracy_given_detection: number | null;
    accuracy_ci: (number | null)[];
    correct: number;
    detected: number;
    per_class: {
      truth_class: string;
      category: Category;
      events: number;
      alerts_with_category: number;
      precision: number | null;
      precision_ci: (number | null)[];
      recall: number | null;
      recall_ci: (number | null)[];
    }[];
    reporting_failures_called_access_gaps: number | null;
    access_gaps_called_reporting: number | null;
    abstention_rate_non_ambiguous: number | null;
    confusion: { rows: string[]; cols: string[]; matrix: number[][] };
  };
  subtype_detail: { class: string; subtype: string; events: number; detected: number; predicted: Record<string, number> }[];
  volume_tiers?: {
    tier: string;
    streams: string[];
    alertable_events: number;
    detected: number;
    recall: number | null;
    alerts: number;
    precision: number | null;
    false_alerts_per_100_stream_weeks: number | null;
    explanation_accuracy: number | null;
  }[];
}

export type Method = "naive" | "robust" | "engine";

export interface EvaluationReport {
  synthetic: boolean;
  notice: string;
  engine_version: string;
  dataset_fingerprint: string;
  protocol: Record<string, string>;
  label_counts: { train: Record<string, number>; test: Record<string, number> };
  observations_used: number;
  observations_excluded_unlabelled: number;
  volume_tiers: Record<string, string>;
  methods: Record<Method, string>;
  tuned_params: Record<Method, Record<string, number>>;
  engine_params_full: Record<string, number | boolean>;
  grid: Record<Method, { params: Record<string, number>; f1: number | null; recall: number | null; precision: number | null; explanation_accuracy?: number | null }[]>;
  train: Record<Method, PeriodMetrics>;
  test: Record<Method, PeriodMetrics>;
  evidence_bands: { band: string; events: number; correct: number; accuracy: number | null }[];
  robustness_missingness: {
    missing_share: number;
    methods: Record<Method, {
      recall: number | null;
      access_gap_recall: number | null;
      access_gap_precision: number | null;
      false_service_gap_alerts_per_100_stream_weeks: number | null;
      false_alerts_per_100_stream_weeks: number | null;
      explanation_accuracy: number | null;
      reporting_alerts: number;
    }>;
  }[];
  operational_replay: {
    grace_days: number;
    replay_days: number;
    alertable_events: number;
    detected: number;
    recall: number | null;
    operational_delay_days: Summ;
    category_correct_at_first_alert: number | null;
    transient_alerts: number;
  }[];
  results_sha256: string;
  duration_seconds: number;
  generated_at: string;
  platform: RuntimeBrief;
  eval_id: string;
}

export interface EvaluationResponse {
  available: boolean;
  report?: EvaluationReport;
  job: Job | null;
  reason?: string;
  stale?: boolean;
  data_mode?: string;
}

export interface SystemInfo {
  runtime: {
    system: string;
    release: string;
    os_pretty_name: string | null;
    machine: string;
    processor: string | null;
    byteorder: string;
    cpu_count: number | null;
    hostname: string;
    python_version: string;
    python_implementation: string;
    python_compiler: string;
    sqlite_version: string;
    flask_version: string | null;
    werkzeug_version: string | null;
    gunicorn_version: string | null;
    ml_packages?: Record<string, string | null>;
    pid: number;
    uptime_seconds: number;
    is_s390x: boolean;
    ibm_z_sysinfo: Record<string, string> | null;
    architecture_verdict: string;
    deployment_label: string | null;
    deployment_label_note: string | null;
  };
  database: {
    engine: string;
    sqlite_version: string;
    path: string;
    size_bytes: number;
    integrity: string;
    journal_mode: string;
    schema_version: string | null;
    row_counts: Record<string, number>;
  };
  data: {
    mode: string | null;
    demo_seed: string | null;
    dataset_fingerprint: string | null;
    synthetic_manifest: Record<string, unknown> | null;
    observations: { records: number; first_date: string | null; last_date: string | null; sites: number; streams: number; by_source: Record<string, number> };
    recent_batches: { batch_id: string; source: string; mode: string; created_at: string; rows_accepted: number; rows_rejected: number }[];
    labels_file_present: boolean;
  };
  analysis: {
    current: { run_id: string; finished_at: string; duration_ms: number; end_date: string; n_observations: number; n_cases: number; engine_version: string; platform: RuntimeBrief } | null;
    history: { run_id: string; finished_at: string; duration_ms: number; n_observations: number; n_cases: number; engine_version: string; machine: string; system: string }[];
  };
  detection_params: { values: Record<string, number | boolean>; source: string };
  audit: { valid: boolean; records: number; first_invalid_id: number | null; head_hash?: string | null };
  config: Record<string, unknown>;
  write_access: { token_required: boolean; client_is_loopback: boolean };
  job: Job | null;
}

export interface SchemaField {
  name: string;
  required: boolean;
  type: string;
  description: string;
}

export interface ImportResult {
  batch_id: string;
  mode: string;
  data_mode: string;
  validation: {
    rows_seen: number;
    rows_accepted: number;
    rows_rejected: number;
    malformed_counts: number;
    missing_counts: number;
    warnings: number;
    issues: { row: number; field: string; message: string; severity: string }[];
    issues_truncated: boolean;
  };
  analysis?: { run_id: string; cases: number };
}

// ---------------------------------------------------------------- optional ML triage benchmark

export type MlVerdict = "no_detectable_difference" | "ml_better" | "rules_better" | "inconclusive";

export interface MlAccuracy {
  correct: number;
  episodes: number;
  accuracy: number | null;
  ci: (number | null)[];
}

export interface MlDisagreement {
  case_id: string;
  site_name: string;
  service_type: string;
  onset: string;
  end: string;
  feed_status: string;
  truth_class: string;
  truth_subtype: string | null;
  truth_category: Category;
  rule_category: Category;
  rule_reason_code: string;
  rule_reason: string;
  ml_category: Category;
  ml_probabilities: Record<string, number>;
  ml_top_features: { feature: string; value: number; typical: number; effect: number }[];
  correct: "rules" | "ml" | "neither";
}

export interface MlTriageReport {
  synthetic: boolean;
  notice: string;
  engine_version: string;
  dataset_fingerprint: string | null;
  protocol: Record<string, string>;
  detector: { params: Record<string, number | boolean>; source: string; episode_scope: string };
  model: {
    name: string;
    spec: Record<string, string | number>;
    features: { name: string; description: string }[];
    classes: string[];
    target: string;
    inputs_excluded: string;
  };
  training: {
    episodes: number;
    by_target: Record<string, number>;
    window: string[];
    built_from: string;
    cv_accuracy: { folds: number[]; mean: number } | null;
    in_sample_accuracy: number | null;
    in_sample_note: string;
  };
  test: {
    window: string[];
    episodes: number;
    by_target: Record<string, number>;
    rules: PeriodMetrics;
    ml: PeriodMetrics;
    paired: {
      episodes: number;
      both_correct: number;
      rules_only_correct: number;
      ml_only_correct: number;
      both_wrong: number;
      mcnemar_exact_p: number;
      rules: MlAccuracy;
      ml: MlAccuracy;
    };
  };
  importances: { feature: string; description: string; impurity: number; permutation_mean: number; permutation_std: number }[];
  sensitivity: {
    other_seeds: { seeds: number[]; correct: number[]; episodes: number; accuracy_min: number | null; accuracy_max: number | null; note: string };
    gradient_boosting: MlAccuracy & { note: string };
    majority_class: MlAccuracy & { class: string; note: string };
    shuffled_label_control: { correct: number[]; episodes: number; accuracy_mean: number | null; accuracy_max: number | null; note: string };
  };
  disagreements: MlDisagreement[];
  conclusion: { verdict: MlVerdict; headline: string; points: string[] };
  production_classifier: { name: string; reason: string };
  results_sha256: string;
  predictions_sha256: string;
  runtime: { machine: string; system: string; python: string; byteorder: string; libraries: Record<string, string> };
  duration_seconds: number;
  generated_at?: string;
}

export interface MlTriageResponse {
  available: boolean;
  reason?: string;
  packages: Record<string, string | null>;
  report?: MlTriageReport | null;
  stale?: boolean;
  job: Job | null;
}
