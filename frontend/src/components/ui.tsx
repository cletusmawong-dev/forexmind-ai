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
    <div className="flex shrink-0 items-center gap-2.5">
      <LogoMark size={size} />
      <div className="whitespace-nowrap leading-tight">
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
    <div className={`mb-3 flex items-center justify-between px-1 ${className}`}>
      <span className="text-[16.5px] font-bold tracking-tight text-white">{children}</span>
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
  const [live, setLive] = React.useState<boolean | null>(null);
  React.useEffect(() => {
    let alive = true;
    const ping = async () => {
      try {
        const r = await fetch("/api/health");
        const d = await r.json();
        if (alive) setLive(d?.demo === false);
      } catch { /* keep last state */ }
    };
    ping();
    const id = setInterval(ping, 60000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  if (live) {
    return (
      <Pill tone="green" className="!px-3 !py-1.5 whitespace-nowrap">
        <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent-green)] anim-pulse" style={{ boxShadow: "0 0 8px rgba(47,217,138,0.9)" }} />
        LIVE · REAL-TIME
      </Pill>
    );
  }
  return (
    <Pill tone="amber" className="!px-3 !py-1.5 whitespace-nowrap">
      <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent-amber)]" style={{ boxShadow: "0 0 8px rgba(245,184,77,0.8)" }} />
      {live === false ? "DEMO · HISTORICAL" : "DEMO · HISTORICAL"}
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
    <div className={`seg-track ${className}`} role="tablist">
      {options.map((o) => (
        <button
          key={o.key}
          role="tab"
          aria-selected={value === o.key}
          onClick={() => onChange(o.key)}
          className={`seg-btn ${value === o.key ? "seg-btn-active" : "hover:text-[var(--text-secondary)]"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className={`chip ${active ? "chip-active" : "hover:text-[var(--text-secondary)]"}`} aria-pressed={active}>
      {children}
    </button>
  );
}

/* ================= progress ================= */
export function ProgressBar({ pct, tone = "blue" }: { pct: number; tone?: "blue" | "green" }) {
  const p = Math.min(100, Math.max(2, pct));
  const from = tone === "blue" ? "#5b9bff" : "#2fd98a";
  const to = tone === "blue" ? "#2b5fe3" : "#1fae67";
  return (
    <div className="progress-track" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
      <div
        className="progress-fill transition-[width] duration-700 ease-out"
        style={{ width: `${p}%`, background: `linear-gradient(to right, ${from}, ${to})`, boxShadow: `0 0 12px ${from}88` }}
      />
      <div
        className="progress-knob transition-[left] duration-700 ease-out"
        style={{ left: `${p}%`, background: from, boxShadow: `0 0 14px ${from}, 0 0 4px rgba(255,255,255,0.8)` }}
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
    <svg viewBox="0 0 220 160" preserveAspectRatio="xMaxYMid slice" className={`pointer-events-none absolute ${className}`} aria-hidden="true">
      <defs>
        <linearGradient id="wv1" x1="0" y1="0" x2="0.6" y2="1">
          <stop offset="0%" stopColor="#4d7cfe" stopOpacity="0.95" />
          <stop offset="48%" stopColor="#8e7bff" stopOpacity="0.85" />
          <stop offset="100%" stopColor="#33d6f6" stopOpacity="0.8" />
        </linearGradient>
        <linearGradient id="wv2" x1="0" y1="0" x2="0.4" y2="1">
          <stop offset="0%" stopColor="#33d6f6" stopOpacity="0.75" />
          <stop offset="100%" stopColor="#8e7bff" stopOpacity="0.45" />
        </linearGradient>
        <filter id="wvblur" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="5" />
        </filter>
        <linearGradient id="wvfadex" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#fff" stopOpacity="0" />
          <stop offset="0.42" stopColor="#fff" stopOpacity="0.95" />
          <stop offset="1" stopColor="#fff" stopOpacity="1" />
        </linearGradient>
        <mask id="wvmask">
          <rect x="0" y="-20" width="220" height="200" fill="url(#wvfadex)" />
        </mask>
      </defs>
      <g className="anim-wave" style={{ transformOrigin: "center" }} mask="url(#wvmask)">
        <path
          d="M168 -20 C 96 26, 208 62, 142 98 C 88 128, 176 146, 150 180"
          fill="url(#wv1)"
          filter="url(#wvblur)"
          opacity="0.42"
        />
        <path
          d="M172 -20 C 100 30, 212 64, 148 100 C 96 130, 182 148, 158 180"
          fill="none"
          stroke="url(#wv1)"
          strokeWidth="3.2"
          strokeLinecap="round"
          opacity="0.95"
        />
        <path
          d="M196 -20 C 128 34, 224 70, 164 104 C 116 132, 198 152, 178 180"
          fill="none"
          stroke="url(#wv2)"
          strokeWidth="2"
          strokeLinecap="round"
          opacity="0.8"
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
