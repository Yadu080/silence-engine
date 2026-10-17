import type { Category, FeedStatus } from "./types";

const nf0 = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 1 });
const compact = new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 1 });

export const num = (v: number | null | undefined, digits = 0): string =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : digits ? nf1.format(v) : nf0.format(v);

export const compactNum = (v: number | null | undefined): string =>
  v === null || v === undefined ? "—" : Math.abs(v) >= 1000 ? compact.format(v) : nf0.format(v);

export const pct = (v: number | null | undefined, digits = 0): string =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;

export const signedPct = (v: number | null | undefined, digits = 1): string => {
  if (v === null || v === undefined) return "—";
  const s = (v * 100).toFixed(digits);
  return v > 0 ? `+${s}%` : v < 0 ? `−${s.replace("-", "")}%` : `${s}%`;
};

const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function parts(iso: string): [number, number, number] {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return [y, m, d];
}

export const day = (iso: string | null | undefined): string => {
  if (!iso) return "—";
  const [y, m, d] = parts(iso);
  return `${d} ${months[m - 1]} ${y}`;
};

export const dayShort = (iso: string): string => {
  const [, m, d] = parts(iso);
  return `${d} ${months[m - 1]}`;
};

export const weekday = (iso: string): string => {
  const [y, m, d] = parts(iso);
  return weekdays[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
};

export const dateTime = (iso: string | null | undefined): string => {
  if (!iso) return "—";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return `${day(iso)} ${dt.toISOString().slice(11, 16)} UTC`;
};

export const range = (a: string, b: string): string => (a === b ? day(a) : `${dayShort(a)} – ${day(b)}`);

export function addDays(iso: string, n: number): string {
  const [y, m, d] = parts(iso);
  const dt = new Date(Date.UTC(y, m - 1, d + n));
  return dt.toISOString().slice(0, 10);
}

export const service = (s: string): string => s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

export const CATEGORY_META: Record<Category, { label: string; short: string; css: string; blurb: string }> = {
  POTENTIAL_ACCESS_GAP: {
    label: "Potential service-access gap",
    short: "Access gap",
    css: "access",
    blurb: "Activity well below expectation while the data feed looks healthy.",
  },
  REPORTING_PROBLEM: {
    label: "Likely reporting problem",
    short: "Reporting",
    css: "reporting",
    blurb: "The silence is plausibly a data-feed artefact; real activity is unknown.",
  },
  UNCERTAIN: {
    label: "Uncertain — investigate",
    short: "Uncertain",
    css: "uncertain",
    blurb: "Evidence is insufficient or conflicting.",
  },
  EXPECTED_VARIATION: {
    label: "Expected variation",
    short: "Expected",
    css: "expected",
    blurb: "Explained by the operating schedule or ordinary variation.",
  },
};

export const CATEGORY_ORDER: Category[] = ["POTENTIAL_ACCESS_GAP", "REPORTING_PROBLEM", "UNCERTAIN", "EXPECTED_VARIATION"];

export const FEED_META: Record<FeedStatus, { label: string; css: string }> = {
  HEALTHY: { label: "Feed healthy", css: "good" },
  DEGRADED: { label: "Feed degraded", css: "warning" },
  FAILED: { label: "Feed failed", css: "critical" },
};

export const REVIEW_LABELS: Record<string, string> = {
  under_review: "Under review",
  verified_access_gap: "Verified: access gap",
  verified_reporting_issue: "Verified: reporting issue",
  verified_expected: "Verified: expected",
  dismissed: "Dismissed",
};

export const TRUTH_LABELS: Record<string, string> = {
  ACCESS_GAP: "Access gap",
  REPORTING_FAILURE: "Reporting failure",
  AMBIGUOUS: "Ambiguous",
  SCHEDULED_CLOSURE: "Scheduled closure",
  NORMAL_VARIATION: "Normal variation",
  NO_EVENT: "No event",
  NOT_FLAGGED: "Not flagged",
};

export const STATUS_LABELS: Record<string, string> = {
  closed: "Closed (normal schedule)",
  closed_unusual: "Closed by schedule (normally open)",
  ok: "Within expected range",
  low: "Below expected (flagged)",
  missing: "No count received",
  suspect: "Count received, feed doubtful",
  no_baseline: "Not enough history yet",
};

export const FLAG_LABELS: Record<string, string> = {
  missing_record: "No record received",
  missing_value: "Record without a count",
  malformed: "Malformed count",
  heartbeat_missing: "Heartbeat missing",
  stale_feed: "Stale feed",
  incomplete_batches: "Incomplete batches",
  zero_fill_suspected: "Zero while feed down",
  flatline: "Flatlined values",
  duplicate: "Duplicate rows",
  duplicate_conflict: "Conflicting duplicates",
  activity_on_closed_day: "Activity on closed day",
  late_arrival: "Late arrival",
};

export const shortHash = (h: string | null | undefined, n = 12): string => (h ? `${h.slice(0, n)}…` : "—");
