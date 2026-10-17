import type { ReactNode } from "react";
import { ApiError } from "../lib/api";
import { CATEGORY_META, FEED_META, REVIEW_LABELS } from "../lib/format";
import type { Category, FeedStatus } from "../lib/types";
import { IconAlert, IconBus, IconCheck, IconInfo, IconPlug, IconWeather, IconX } from "./Icons";

export function CategoryBadge({ category, short = false }: { category: Category; short?: boolean }) {
  const m = CATEGORY_META[category];
  return (
    <span className={`badge cat-${m.css}`} title={m.blurb}>
      <span className="key" aria-hidden="true" />
      {short ? m.short : m.label}
    </span>
  );
}

export function FeedBadge({ status }: { status: FeedStatus }) {
  const m = FEED_META[status];
  const Icon = status === "HEALTHY" ? IconCheck : status === "DEGRADED" ? IconAlert : IconX;
  return (
    <span className={`badge st-${m.css}`}>
      <Icon className="ico" size={12} />
      {m.label}
    </span>
  );
}

export function ReviewBadge({ status }: { status: string | null }) {
  if (!status) return <span className="muted">Not reviewed</span>;
  return <span className="badge plain">{REVIEW_LABELS[status] ?? status}</span>;
}

export function Meter({ value, label }: { value: number; label: string }) {
  const band = value >= 70 ? "high" : value >= 45 ? "mid" : "low";
  return (
    <span className={`meter ${band}`} role="img" aria-label={`${label} ${value} of 100`}>
      <span className="val">{value}</span>
      <span className="track">
        <span className="fill" style={{ width: `${Math.max(3, value)}%` }} />
      </span>
    </span>
  );
}

export function ContextIcons({ weather, transport, outage }: { weather: number; transport: number; outage: number }) {
  if (!weather && !transport && !outage) return <span className="muted">None recorded</span>;
  return (
    <span className="ctx-icons">
      {weather > 0 && (
        <span title={`Severe weather on ${weather} day(s)`}>
          <IconWeather size={14} />
          <span className="num">{weather}d</span>
          <span className="sr-only">severe weather days</span>
        </span>
      )}
      {transport > 0 && (
        <span title={`Transport disruption on ${transport} day(s)`}>
          <IconBus size={14} />
          <span className="num">{transport}d</span>
          <span className="sr-only">transport disruption days</span>
        </span>
      )}
      {outage > 0 && (
        <span title={`Reported outage on ${outage} day(s)`}>
          <IconPlug size={14} />
          <span className="num">{outage}d</span>
          <span className="sr-only">reported outage days</span>
        </span>
      )}
    </span>
  );
}

export function Skeleton({ height = 16, width = "100%" }: { height?: number; width?: number | string }) {
  return <div className="skeleton" style={{ height, width }} aria-hidden="true" />;
}

export function LoadingBlock({ rows = 4, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-live="polite" style={{ display: "grid", gap: 10 }}>
      <span className="sr-only">{label}…</span>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} height={i === 0 ? 28 : 16} width={i === 0 ? "45%" : `${92 - i * 9}%`} />
      ))}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: ApiError | Error; onRetry?: () => void }) {
  const code = error instanceof ApiError ? error.code : "error";
  return (
    <div className="notice error" role="alert">
      <IconAlert size={16} />
      <div style={{ display: "grid", gap: 8 }}>
        <div>
          <strong>Could not load this view.</strong> {error.message}{" "}
          <span className="muted mono">({code})</span>
        </div>
        {onRetry && (
          <div>
            <button className="btn small" onClick={onRetry}>
              Try again
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <div className="prose">{children}</div>}
      {action}
    </div>
  );
}

export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" | "error" }) {
  return (
    <div className={`notice ${tone === "info" ? "" : tone}`} role={tone === "error" ? "alert" : undefined}>
      {tone === "info" ? <IconInfo size={16} /> : <IconAlert size={16} />}
      <div>{children}</div>
    </div>
  );
}

export function Seg<T extends string | number>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={String(o.value)} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
