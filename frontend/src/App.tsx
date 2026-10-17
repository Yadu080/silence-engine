import { useEffect, useState } from "react";
import { Notice } from "./components/Bits";
import {
  BrandMark,
  IconEval,
  IconFeed,
  IconMap,
  IconMenu,
  IconOverview,
  IconQueue,
  IconServer,
} from "./components/Icons";
import { SpatialBackground } from "./components/SpatialBackground";
import { SvgDefs } from "./components/SilenceChart";
import { api } from "./lib/api";
import { day } from "./lib/format";
import { href, useRoute } from "./lib/hooks";
import type { Health } from "./lib/types";
import { CaseView } from "./views/CaseView";
import { DataQualityView } from "./views/DataQualityView";
import { EvaluationView } from "./views/EvaluationView";
import { InvestigationsView } from "./views/InvestigationsView";
import { OverviewView } from "./views/OverviewView";
import { PlatformView } from "./views/PlatformView";
import { SilenceMapView } from "./views/SilenceMapView";

const NAV = [
  { path: "/", label: "Overview", icon: IconOverview },
  { path: "/silence", label: "Silence map", icon: IconMap },
  { path: "/cases", label: "Investigations", icon: IconQueue },
  { path: "/data-quality", label: "Data quality", icon: IconFeed },
  { path: "/evaluation", label: "Evaluation", icon: IconEval },
  { path: "/platform", label: "Platform", icon: IconServer },
];

export default function App() {
  const route = useRoute();
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  const [dataVersion, setDataVersion] = useState<string>("");

  // Poll health: fast while a background job runs, slow otherwise.
  useEffect(() => {
    let timer: number | undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const h = await api.get<Health>("/api/health");
        if (cancelled) return;
        setHealth(h);
        setHealthError(null);
        setDataVersion(h.analysis_finished_at ?? "");
        timer = window.setTimeout(tick, h.job?.status === "running" || !h.analysis_ready ? 1500 : 15000);
      } catch (e) {
        if (cancelled) return;
        setHealthError(e instanceof Error ? e.message : String(e));
        timer = window.setTimeout(tick, 4000);
      }
    };
    tick();
    const now = () => {
      window.clearTimeout(timer);
      tick();
    };
    window.addEventListener("silence:health", now);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      window.removeEventListener("silence:health", now);
    };
  }, []);

  useEffect(() => {
    setNavOpen(false);
    window.scrollTo({ top: 0 });
  }, [route.path]);

  const section = "/" + (route.segments[0] ?? "");
  const current = NAV.find((n) => n.path === section) ?? NAV[0];
  const ready = health?.analysis_ready;

  let page: JSX.Element;
  if (!health && !healthError) {
    page = <div className="boot" aria-busy="true" />;
  } else if (healthError && !health) {
    page = (
      <div className="boot">
        <div className="boot-card">
          <h1>Backend unreachable</h1>
          <Notice tone="error">
            {healthError} Start it with <code>./scripts/dev_backend.sh</code> (or <code>./scripts/demo.sh</code>) and this page will
            reconnect automatically.
          </Notice>
        </div>
      </div>
    );
  } else if (!ready && section !== "/platform") {
    page = <Booting health={health!} />;
  } else {
    const key = dataVersion;
    switch (section) {
      case "/silence":
        page = <SilenceMapView key={key} />;
        break;
      case "/cases":
        page = route.segments[1] ? <CaseView key={`${key}-${route.segments[1]}`} caseId={route.segments[1]} /> : <InvestigationsView key={key} query={route.query} />;
        break;
      case "/data-quality":
        page = <DataQualityView key={key} />;
        break;
      case "/evaluation":
        page = <EvaluationView key={key} health={health!} />;
        break;
      case "/platform":
        page = <PlatformView key={key} health={health!} />;
        break;
      default:
        page = <OverviewView key={key} />;
    }
  }

  const synthetic = health?.synthetic_active ?? true;

  return (
    <>
      <SpatialBackground />
      <SvgDefs />
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className={`synthetic-strip ${synthetic ? "" : "imported"}`} role="note">
        {synthetic ? (
          <span>
            <strong>SYNTHETIC DATA</strong> — fictional locations and events for demonstration and evaluation. Nothing here is a
            real-world finding.
          </span>
        ) : (
          <span>
            <strong>IMPORTED DATA</strong> — alerts are hypotheses for human verification, not findings.
          </span>
        )}
      </div>
      <div className="app">
        {navOpen && <div className="scrim" onClick={() => setNavOpen(false)} />}
        <aside className={`rail ${navOpen ? "open" : ""}`} aria-label="Primary">
          <a className="brand" href={href("/")}>
            <BrandMark className="brand-mark" />
            <span className="brand-name">
              Silence Engine
              <span className="brand-tag">What should have happened, but didn’t</span>
            </span>
          </a>
          <nav className="nav">
            {NAV.map((n) => (
              <a key={n.path} href={href(n.path)} aria-current={current.path === n.path ? "page" : undefined}>
                <n.icon size={17} />
                {n.label}
              </a>
            ))}
          </nav>
          <div className="rail-foot">
            <p>Alerts are hypotheses for verification. Aggregate data only — no personal identifiers.</p>
            {health && (
              <div className="runtime" title="Reported by the backend at runtime">
                <span>
                  Backend <b>{health.runtime.machine}</b> · {health.runtime.system}
                </span>
                <span>
                  Python {health.runtime.python} · engine {health.engine_version}
                </span>
              </div>
            )}
          </div>
        </aside>
        <div className="main">
          <header className="topbar">
            <button className="btn icon ghost menu-btn" aria-label="Open navigation" aria-expanded={navOpen} onClick={() => setNavOpen(true)}>
              <IconMenu size={18} />
            </button>
            <div className="crumbs">
              <span className="here">{current.label}</span>
            </div>
            <div className="spacer" />
            {health?.job?.status === "running" && (
              <span className="job-pill" role="status">
                <span className="spinner" aria-hidden="true" />
                {health.job.message}
              </span>
            )}
            {health?.as_of && (
              <span className="asof">
                <span className="long">Data as of </span>
                <b>{day(health.as_of)}</b>
              </span>
            )}
          </header>
          <main id="main" className="content" tabIndex={-1}>
            {page}
          </main>
        </div>
      </div>
    </>
  );
}

function Booting({ health }: { health: Health }) {
  const job = health.job;
  return (
    <div className="boot" style={{ minHeight: "60vh" }}>
      <div className="boot-card">
        <h1>Preparing the synthetic demonstration</h1>
        <p className="prose">
          The backend is generating a reproducible synthetic dataset, evaluating the method against held-out scenarios and
          running the detector. This takes about 20 seconds the first time.
        </p>
        {job?.status === "running" && (
          <>
            <div className="progress-line" aria-hidden="true" />
            <p className="muted" role="status">
              {job.message}…
            </p>
          </>
        )}
        {job?.status === "failed" && <Notice tone="error">Initialisation failed: {job.error?.message}</Notice>}
        {!job && !health.data_loaded && (
          <Notice>
            No data is loaded. Generate the demo dataset from the <a href={href("/platform")}>Platform</a> page, or run{" "}
            <code>python manage.py init-demo</code>.
          </Notice>
        )}
      </div>
    </div>
  );
}
