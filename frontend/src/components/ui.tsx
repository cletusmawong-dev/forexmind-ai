import React, { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { GraduationCap } from "lucide-react";

/* ================= brand ================= */
export function LogoMark({ size = 34 }: { size?: number }) {
  return (
    <div
      className="flex items-center justify-center rounded-[30%] border border-white/[0.12]"
      style={{
        width: size,
        height: size,
        background: "linear-gradient(145deg, rgba(62,123,250,0.35), rgba(20,30,60,0.7))",
        boxShadow: "0 6px 20px rgba(62,124,250,0.3), inset 0 1px 0 rgba(255,255,255,0.15)",
      }}
    >
      <GraduationCap size={size * 0.6} className="text-[#8fb4ff]" strokeWidth={2} />
    </div>
  );
}

export function Logo({ size = 34, subtitle }: { size?: number; subtitle?: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <LogoMark size={size} />
      <div className="leading-tight">
        <div className="text-[15px] font-bold tracking-[0.06em]">
          FOREXMIND <span className="text-grad-blue">AI</span>
        </div>
        {subtitle && <div className="text-[9px] tracking-[0.18em] text-[var(--text-muted)]">{subtitle}</div>}
      </div>
    </div>
  );
}

/* ================= glass surfaces ================= */
export function Glass({
  children,
  level = 1,
  className = "",
  pad = true,
}: {
  children: React.ReactNode;
  level?: 1 | 2 | "float";
  className?: string;
  pad?: boolean;
}) {
  const cls = level === 1 ? "glass" : level === 2 ? "glass-2" : "glass-float";
  return <section className={`${cls} ${pad ? "p-5" : ""} ${className}`}>{children}</section>;
}

export function Divider({ className = "" }: { className?: string }) {
  return <div className={`divider ${className}`} />;
}

export function PageHeader({ title, sub, right }: { title: string; sub?: string; right?: React.ReactNode }) {
  return (
    <header className="mb-6 flex items-start justify-between gap-3">
      <div>
        <h1 className="text-[27px] font-bold leading-tight tracking-[-0.02em]">{title}</h1>
        {sub && <p className="mt-1 text-[12.5px] text-[var(--text-secondary)]">{sub}</p>}
      </div>
      {right}
    </header>
  );
}

export function SectionHeader({ children, right, className = "" }: { children: React.ReactNode; right?: React.ReactNode; className?: string }) {
  return (
    <div className={`mb-2.5 flex items-center justify-between px-1 ${className}`}>
      <span className="eyebrow">{children}</span>
      {right}
    </div>
  );
}

/* ================= status & pills ================= */
export function StatusDot({
  tone = "green",
  pulse = true,
  size = 8,
}: {
  tone?: "green" | "red" | "amber" | "blue" | "cyan" | "purple";
  pulse?: boolean;
  size?: number;
}) {
  const hex: Record<string, string> = {
    green: "var(--accent-green)",
    red: "var(--accent-red)",
    amber: "var(--accent-amber)",
    blue: "var(--accent-blue)",
    cyan: "var(--accent-cyan)",
    purple: "var(--accent-purple)",
  };
  return (
    <span className="relative inline-flex shrink-0" style={{ width: size, height: size }}>
      {pulse && (
        <span className="absolute inset-0 rounded-full anim-pulse" style={{ background: hex[tone], transform: "scale(2)" , opacity: .4 }} />
      )}
      <span
        className="relative inline-flex h-full w-full rounded-full"
        style={{ background: hex[tone], boxShadow: `0 0 10px ${hex[tone]}` }}
      />
    </span>
  );
}

const pillTones: Record<string, string> = {
  blue: "border-[rgba(77,124,254,0.35)] bg-[rgba(77,124,254,0.12)] text-[#8fb4ff]",
  cyan: "border-[rgba(51,214,246,0.3)] bg-[rgba(51,214,246,0.1)] text-[var(--accent-cyan)]",
  green: "border-[rgba(47,217,138,0.32)] bg-[rgba(47,217,138,0.1)] text-[var(--accent-green)]",
  red: "border-[rgba(251,77,106,0.32)] bg-[rgba(251,77,106,0.1)] text-[var(--accent-red)]",
  purple: "border-[rgba(142,123,255,0.32)] bg-[rgba(142,123,255,0.12)] text-[#b3a6ff]",
  amber: "border-[rgba(245,184,77,0.32)] bg-[rgba(245,184,77,0.1)] text-[var(--accent-amber)]",
  neutral: "border-white/[0.1] bg-white/[0.05] text-[var(--text-secondary)]",
  /* legacy aliases kept so earlier screens stay on the design system */
  pos: "border-[rgba(47,217,138,0.32)] bg-[rgba(47,217,138,0.1)] text-[var(--accent-green)]",
  neg: "border-[rgba(251,77,106,0.32)] bg-[rgba(251,77,106,0.1)] text-[var(--accent-red)]",
  violet: "border-[rgba(142,123,255,0.32)] bg-[rgba(142,123,255,0.12)] text-[#b3a6ff]",
  warn: "border-[rgba(245,184,77,0.32)] bg-[rgba(245,184,77,0.1)] text-[var(--accent-amber)]",
  acc: "border-[rgba(77,124,254,0.35)] bg-[rgba(77,124,254,0.12)] text-[#8fb4ff]",
};
export type PillTone = keyof typeof pillTones;

export function Pill({ tone = "neutral", children, className = "" }: { tone?: PillTone; children: React.ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[10px] font-semibold tracking-wide ${pillTones[tone]} ${className}`}>
      {children}
    </span>
  );
}

export function DemoTag() {
  return (
    <Pill tone="amber" className="!px-3 !py-1.5 whitespace-nowrap">
      <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent-amber)]" style={{ boxShadow: "0 0 8px rgba(245,184,77,0.8)" }} />
      DEMO · HISTORICAL
    </Pill>
  );
}

/* ================= segmented control ================= */
export function Segmented({
  options,
  value,
  onChange,
  className = "",
}: {
  options: { key: string; label: string }[];
  value: string;
  onChange: (k: string) => void;
  className?: string;
}) {
  return (
    <div className={`glass-2 flex rounded-full !p-1 ${className}`}>
      {options.map((o) => (
        <button
          key={o.key}
          onClick={() => onChange(o.key)}
          className={`flex-1 whitespace-nowrap rounded-full px-4 py-2.5 text-[12.5px] font-semibold transition-all duration-300 ${
            value === o.key
              ? "bg-gradient-to-b from-[rgba(77,124,254,0.4)] to-[rgba(77,124,254,0.18)] text-white glow-blue"
              : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`shrink-0 rounded-full border px-3.5 py-2 text-[11.5px] font-semibold transition-all duration-300 ${
        active
          ? "border-[rgba(77,124,254,0.55)] bg-[rgba(77,124,254,0.16)] text-[#a8c4ff] glow-blue"
          : "border-white/[0.08] bg-white/[0.03] text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
      }`}
    >
      {children}
    </button>
  );
}

/* ================= progress ================= */
export function ProgressBar({ pct, tone = "blue" }: { pct: number; tone?: "blue" | "green" }) {
  const p = Math.min(100, Math.max(2, pct));
  const from = tone === "blue" ? "#3e7bfa" : "#2fd98a";
  const to = tone === "blue" ? "#33d6f6" : "#33d6f6";
  return (
    <div className="relative h-[7px] w-full rounded-full bg-white/[0.07]">
      <div
        className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-700 ease-out"
        style={{ width: `${p}%`, background: `linear-gradient(to right, ${from}, ${to})`, boxShadow: `0 0 14px ${from}66` }}
      />
      <div
        className="absolute top-1/2 h-[13px] w-[13px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-white transition-[left] duration-700 ease-out"
        style={{ left: `${p}%`, boxShadow: `0 0 16px ${from}, 0 0 5px #fff` }}
      />
    </div>
  );
}

/* ================= metric grid ================= */
export function MetricGrid({ items }: { items: { label: string; value: string; sub?: string; tone?: string }[] }) {
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.05] sm:grid-cols-4">
      {items.map((m, i) => (
        <div key={i} className="bg-[rgba(10,15,30,0.72)] px-3 py-4 text-center">
          <div className="text-[8.5px] font-semibold uppercase tracking-[0.14em] text-[var(--text-muted)]">{m.label}</div>
          <div className={`num mt-1.5 text-[16px] font-semibold ${m.tone || "text-[var(--text-primary)]"}`}>{m.value}</div>
          {m.sub && <div className="num mt-0.5 text-[10px] text-[var(--text-muted)]">{m.sub}</div>}
        </div>
      ))}
    </div>
  );
}

/* ================= expandable ================= */
export function Expandable({
  summary,
  children,
  open,
  onToggle,
}: {
  summary: React.ReactNode;
  children: React.ReactNode;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="glass overflow-hidden">
      <button onClick={onToggle} className="tap flex w-full items-center justify-between gap-3 px-5 py-4 text-left">
        <div className="min-w-0 flex-1">{summary}</div>
        <ChevronDown size={16} className={`shrink-0 text-[var(--text-muted)] transition-transform duration-500 ${open ? "rotate-180" : ""}`} />
      </button>
      <div className={`grid transition-all duration-500 ease-out ${open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
        <div className="overflow-hidden">
          <div className="px-5 pb-5">{children}</div>
        </div>
      </div>
    </div>
  );
}

/* ================= feedback ================= */
export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-20">
      <div className="h-8 w-8 animate-spin rounded-full border-[2.5px] border-white/[0.08] border-t-[var(--accent-blue)]" />
      {label && <span className="text-[12px] text-[var(--text-secondary)]">{label}</span>}
    </div>
  );
}

export function Empty({ title, sub, icon }: { title: string; sub?: string; icon?: React.ReactNode }) {
  return (
    <Glass level={2} className="py-12 text-center">
      {icon && <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.03] text-[var(--text-muted)]">{icon}</div>}
      <div className="text-[14px] font-semibold">{title}</div>
      {sub && <div className="mx-auto mt-1.5 max-w-[280px] text-[12px] leading-relaxed text-[var(--text-muted)]">{sub}</div>}
    </Glass>
  );
}

export function ConnectionState({ onRetry, label = "Can't reach your agent" }: { onRetry?: () => void; label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center anim-fadeIn">
      <div className="relative mb-5">
        <span className="absolute inset-0 rounded-full bg-[rgba(245,184,77,0.25)] blur-xl anim-pulse" style={{ transform: "scale(1.6)" }} />
        <span className="relative flex h-11 w-11 items-center justify-center rounded-full border border-[rgba(245,184,77,0.3)] bg-[rgba(245,184,77,0.08)] text-[var(--accent-amber)]">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M2 8.5a15 15 0 0 1 20 0M5.5 12a10 10 0 0 1 13 0M9 15.5a5 5 0 0 1 6 0" />
            <path d="M12 19.2v.1" />
          </svg>
        </span>
      </div>
      <div className="text-[14px] font-semibold">{label}</div>
      <p className="mx-auto mt-1.5 max-w-[260px] text-[11.5px] leading-relaxed text-[var(--text-muted)]">
        The server may be waking up or restarting. Retrying automatically — your research data is safe.
      </p>
      {onRetry && (
        <button className="btn-ghost mt-5" onClick={onRetry}>
          Try now
        </button>
      )}
    </div>
  );
}

/* ================= animated numbers ================= */
export function AnimatedNumber({
  value,
  format = (v: number) => v.toFixed(2),
  className = "",
  duration = 750,
}: {
  value: number;
  format?: (v: number) => string;
  className?: string;
  duration?: number;
}) {
  const [display, setDisplay] = useState(value);
  const prev = useRef(value);
  useEffect(() => {
    const from = prev.current;
    prev.current = value;
    if (from === value) {
      setDisplay(value);
      return;
    }
    let raf = 0;
    const start = performance.now();
    const step = (t: number) => {
      const p = Math.min(1, (t - start) / duration);
      const e = 1 - Math.pow(1 - p, 4);
      setDisplay(from + (value - from) * e);
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);
  return <span className={`num ${className}`}>{format(display)}</span>;
}

/* ================= hero wave graphic ================= */
export function WaveGraphic({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 200 120" className={`pointer-events-none absolute ${className}`} aria-hidden="true">
      <defs>
        <linearGradient id="wv1" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#3e7bfa" stopOpacity="0.9" />
          <stop offset="55%" stopColor="#7c5cff" stopOpacity="0.65" />
          <stop offset="100%" stopColor="#33d6f6" stopOpacity="0.8" />
        </linearGradient>
        <linearGradient id="wv2" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#33d6f6" stopOpacity="0.5" />
          <stop offset="100%" stopColor="#8e7bff" stopOpacity="0.35" />
        </linearGradient>
        <filter id="wvblur" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="3.2" />
        </filter>
      </defs>
      <g className="anim-wave" style={{ transformOrigin: "center" }}>
        <path
          d="M-10 78 C 30 18, 62 110, 104 52 S 168 8, 210 44 L 210 -10 L -10 -10 Z"
          fill="url(#wv1)"
          filter="url(#wvblur)"
          opacity="0.85"
        />
        <path
          d="M-10 92 C 36 40, 70 118, 112 66 S 176 26, 210 58"
          fill="none"
          stroke="url(#wv2)"
          strokeWidth="2.4"
          strokeLinecap="round"
          opacity="0.9"
        />
      </g>
    </svg>
  );
}

export function ChevronSection({ open }: { open: boolean }) {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" className={`text-[var(--text-muted)] transition-transform duration-500 ${open ? "rotate-180" : ""}`}>
      <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* ================= shared primitives (v2) ================= */

/** Small uppercase label — the standard micro-heading. */
export function Eyebrow({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`eyebrow ${className}`}>{children}</div>;
}

const glowDotTones: Record<string, { bg: string; shadow: string }> = {
  blue: { bg: "var(--accent-blue)", shadow: "rgba(77,124,254,0.9)" },
  acc: { bg: "var(--accent-blue)", shadow: "rgba(77,124,254,0.9)" },
  cyan: { bg: "var(--accent-cyan)", shadow: "rgba(51,214,246,0.9)" },
  green: { bg: "var(--accent-green)", shadow: "rgba(47,217,138,0.9)" },
  pos: { bg: "var(--accent-green)", shadow: "rgba(47,217,138,0.9)" },
  red: { bg: "var(--accent-red)", shadow: "rgba(251,77,106,0.9)" },
  neg: { bg: "var(--accent-red)", shadow: "rgba(251,77,106,0.9)" },
  purple: { bg: "var(--accent-purple)", shadow: "rgba(142,123,255,0.9)" },
  violet: { bg: "var(--accent-purple)", shadow: "rgba(142,123,255,0.9)" },
  amber: { bg: "var(--accent-amber)", shadow: "rgba(245,184,77,0.9)" },
  warn: { bg: "var(--accent-amber)", shadow: "rgba(245,184,77,0.9)" },
};

/** Glowing status dot used across timelines, toasts and lists. */
export function GlowDot({ tone = "blue", size = 7, pulse = true }: { tone?: string; size?: number; pulse?: boolean }) {
  const t = glowDotTones[tone] ?? glowDotTones.blue;
  return (
    <span className="relative inline-flex shrink-0" style={{ width: size, height: size }}>
      {pulse && <span className="absolute inset-0 rounded-full anim-pulse" style={{ background: t.bg, opacity: 0.4, transform: "scale(2)" }} />}
      <span
        className="relative inline-block rounded-full"
        style={{ width: size, height: size, background: t.bg, boxShadow: `0 0 ${size * 1.7}px ${t.shadow}` }}
      />
    </span>
  );
}
