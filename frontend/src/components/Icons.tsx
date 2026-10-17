import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { size?: number };

function Base({ size = 16, children, ...rest }: P) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const IconOverview = (p: P) => (
  <Base {...p}>
    <path d="M4 19V9M9 19V5M14 19v-3M19 19v-8" />
    <path d="M14 13v-1.5" strokeDasharray="0.5 2.5" />
  </Base>
);
export const IconMap = (p: P) => (
  <Base {...p}>
    <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
    <path d="M3.5 9.5h17M3.5 14.5h17M9 4.5v15M14.5 4.5v15" />
  </Base>
);
export const IconQueue = (p: P) => (
  <Base {...p}>
    <path d="M8 6h12M8 12h12M8 18h8" />
    <circle cx="4" cy="6" r="1" />
    <circle cx="4" cy="12" r="1" />
    <circle cx="4" cy="18" r="1" />
  </Base>
);
export const IconFeed = (p: P) => (
  <Base {...p}>
    <path d="M4 12h3l2-5 3 10 2-5h6" />
  </Base>
);
export const IconEval = (p: P) => (
  <Base {...p}>
    <path d="M4 20h16" />
    <path d="M6 16l4-5 3 3 5-7" />
  </Base>
);
export const IconServer = (p: P) => (
  <Base {...p}>
    <rect x="4" y="4" width="16" height="7" rx="1.5" />
    <rect x="4" y="13" width="16" height="7" rx="1.5" />
    <path d="M8 7.5h.01M8 16.5h.01" strokeWidth={2.4} />
  </Base>
);
export const IconSun = (p: P) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" />
  </Base>
);
export const IconMoon = (p: P) => (
  <Base {...p}>
    <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
  </Base>
);
export const IconAuto = (p: P) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 4v16" />
    <path d="M12 4a8 8 0 0 1 0 16Z" fill="currentColor" stroke="none" />
  </Base>
);
export const IconMenu = (p: P) => (
  <Base {...p}>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </Base>
);
export const IconInfo = (p: P) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 11v5M12 8h.01" />
  </Base>
);
export const IconAlert = (p: P) => (
  <Base {...p}>
    <path d="M12 4 2.8 19.5h18.4Z" />
    <path d="M12 10v4.5M12 17h.01" />
  </Base>
);
export const IconCheck = (p: P) => (
  <Base {...p}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Base>
);
export const IconX = (p: P) => (
  <Base {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Base>
);
export const IconWeather = (p: P) => (
  <Base {...p}>
    <path d="M7 16a4 4 0 1 1 .9-7.9A5 5 0 0 1 17.5 9 3.5 3.5 0 0 1 17 16Z" />
    <path d="M9 19l-1 2M13 19l-1 2M17 19l-1 2" />
  </Base>
);
export const IconBus = (p: P) => (
  <Base {...p}>
    <rect x="5" y="4" width="14" height="13" rx="2" />
    <path d="M5 11h14M8 20v-3M16 20v-3M8.5 14h.01M15.5 14h.01" />
  </Base>
);
export const IconPlug = (p: P) => (
  <Base {...p}>
    <path d="M9 3v5M15 3v5M7 8h10v3a5 5 0 0 1-10 0Z M12 16v5" />
  </Base>
);
export const IconArrow = (p: P) => (
  <Base {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Base>
);
export const IconBack = (p: P) => (
  <Base {...p}>
    <path d="M19 12H5M11 6l-6 6 6 6" />
  </Base>
);
export const IconRefresh = (p: P) => (
  <Base {...p}>
    <path d="M20 11a8 8 0 0 0-14.6-4.5M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5M20 20v-4h-4" />
  </Base>
);
export const IconDownload = (p: P) => (
  <Base {...p}>
    <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />
  </Base>
);

export function BrandMark({ className }: { className?: string }) {
  // Activity bars with one expected bar that never arrived (outlined, dotted).
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="var(--ink)" />
      <g strokeLinecap="round" strokeWidth="3" fill="none">
        <path d="M7.5 21v-6" stroke="var(--inverse)" />
        <path d="M12.5 22.5v-12" stroke="var(--inverse)" />
        <path d="M17.5 22.5v-9" stroke="var(--access)" strokeDasharray="0.01 4.5" />
        <path d="M22.5 22.5V12" stroke="var(--inverse)" />
      </g>
      <path d="M5 25.5h22" stroke="var(--inverse)" strokeOpacity="0.35" strokeWidth="1.2" />
    </svg>
  );
}
