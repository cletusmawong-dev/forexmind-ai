import React, { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Logo } from "./Logo";

/* ---------------- ambient orb: the AI presence ---------------- */
export function Orb({ size = 88, dim = false }: { size?: number; dim?: boolean }) {
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <div
        className={`absolute inset-0 rounded-full blur-2xl ${dim ? "opacity-40" : "opacity-70 animate-breathe"}`}
        style={{ background: "conic-gradient(from 210deg, #6C9EFF, #A79BF7, #7CD5F2, #6C9EFF)" }}
      />
      <div className="absolute inset-[5px] rounded-full border border-white/10 bg-base-2/80 backdrop-blur-2xl shadow-float flex items-center justify-center">
        <Logo size={size * 0.52} withText={false} />
      </div>
    </div>
  );
}

/* ---------------- glass surfaces ---------------- */
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

export function GlowDot({ tone = "pos", pulse = true, size = 7 }: { tone?: "pos" | "neg" | "warn" | "acc" | "violet" | "cyan"; pulse?: boolean; size?: number }) {
  const map: Record<string, string> = {
    pos: "bg-pos",
    neg: "bg-neg",
    warn: "bg-warn",
    acc: "bg-acc",
    violet: "bg-acc-violet",
    cyan: "bg-acc-cyan",
  };
  const glow: Record<string, string> = {
    pos: "0 0 10px rgba(62,207,142,0.8)",
    neg: "0 0 10px rgba(240,120,140,0.8)",
    warn: "0 0 10px rgba(229,181,103,0.8)",
    acc: "0 0 10px rgba(108,158,255,0.8)",
    violet: "0 0 10px rgba(167,155,247,0.8)",
    cyan: "0 0 10px rgba(124,213,242,0.8)",
  };
  return (
    <span className="relative inline-flex shrink-0" style={{ width: size, height: size }}>
      {pulse && <span className={`absolute inset-0 rounded-full ${map[tone]} opacity-40 animate-pulseSoft`} style={{ transform: "scale(1.9)" }} />}
      <span className={`relative inline-flex h-full w-full rounded-full ${map[tone]}`} style={{ boxShadow: glow[tone] }} />
    </span>
  );
}

export function Eyebrow({ children, right, className = "" }: { children: React.ReactNode; right?: React.ReactNode; className?: string }) {
  return (
    <div className={`flex items-baseline justify-between px-1 ${className}`}>
      <span className="eyebrow">{children}</span>
      {right}
    </div>
  );
}

export function DemoTag() {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border border-warn/20 bg-warn/[0.07] px-2.5 py-1 text-[9.5px] font-semibold uppercase tracking-[0.12em] text-warn/90"
      title="Replayed historical DEMO dataset — never presented as live market data"
    >
      <span className="h-1 w-1 rounded-full bg-warn/80" />
      Demo · Historical
    </span>
  );
}

/* ---------------- typography-led numbers ---------------- */
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
  return (
    <span className={`num ${className}`}>{format(display)}</span>
  );
}

/* thin elegant goal indicator with a glowing position dot */
export function ThinProgress({ pct, tone = "acc" }: { pct: number; tone?: "acc" | "pos" }) {
  const p = Math.min(100, Math.max(1.5, pct));
  const from = tone === "acc" ? "#6C9EFF" : "#3ECF8E";
  const to = tone === "acc" ? "#7CD5F2" : "#7CD5F2";
  return (
    <div className="relative h-[3px] w-full rounded-full bg-white/[0.07]">
      <div
        className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-700 ease-out"
        style={{ width: `${p}%`, background: `linear-gradient(to right, ${from}, ${to})`, boxShadow: `0 0 12px ${from}55` }}
      />
      <div
        className="absolute top-1/2 h-[9px] w-[9px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-white transition-[left] duration-700 ease-out"
        style={{ left: `${p}%`, boxShadow: `0 0 14px ${from}, 0 0 4px #fff` }}
      />
    </div>
  );
}

/* ---------------- segmented pill control ---------------- */
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
    <div className={`flex rounded-full border border-white/[0.07] bg-white/[0.03] p-1 backdrop-blur-xl ${className}`}>
      {options.map((o) => (
        <button
          key={o.key}
          onClick={() => onChange(o.key)}
          className={`flex-1 whitespace-nowrap rounded-full px-4 py-2 text-[12px] font-medium transition-all duration-300 ${
            value === o.key ? "bg-white/[0.1] text-txt-hi shadow-soft" : "text-txt-low hover:text-txt-mid"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------- smooth expandable section ---------------- */
export function Expandable({
  summary,
  children,
  open,
  onToggle,
  hint,
}: {
  summary: React.ReactNode;
  children: React.ReactNode;
  open: boolean;
  onToggle: () => void;
  hint?: string;
}) {
  return (
    <div className="glass-2 overflow-hidden">
      <button onClick={onToggle} className="tap flex w-full items-center justify-between gap-3 px-5 py-4 text-left">
        <div className="min-w-0 flex-1">{summary}</div>
        <ChevronDown size={16} className={`shrink-0 text-txt-low transition-transform duration-500 ${open ? "rotate-180" : ""}`} />
      </button>
      <div className={`grid transition-all duration-500 ease-out ${open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
        <div className="overflow-hidden">
          <div className="px-5 pb-5 pt-0">{children}</div>
        </div>
      </div>
      {!open && hint && <div className="px-5 pb-4 text-[11px] text-txt-faint">{hint}</div>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-20">
      <div className="h-8 w-8 animate-spin rounded-full border-[2.5px] border-white/[0.08] border-t-acc/80" />
      {label && <span className="text-[12px] text-txt-low">{label}</span>}
    </div>
  );
}

export function Empty({ title, sub, icon }: { title: string; sub?: string; icon?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2.5 py-14 text-center">
      {icon && <div className="mb-1 text-txt-faint">{icon}</div>}
      <div className="text-[14px] font-medium text-txt-mid">{title}</div>
      {sub && <div className="max-w-[280px] text-[12px] leading-relaxed text-txt-faint">{sub}</div>}
    </div>
  );
}

/* refined pill badge */
const toneMap: Record<string, string> = {
  pos: "border-pos/25 bg-pos/[0.09] text-pos",
  neg: "border-neg/25 bg-neg/[0.09] text-neg",
  warn: "border-warn/25 bg-warn/[0.09] text-warn",
  acc: "border-acc/25 bg-acc/[0.09] text-acc",
  violet: "border-acc-violet/25 bg-acc-violet/[0.09] text-acc-violet",
  cyan: "border-acc-cyan/25 bg-acc-cyan/[0.09] text-acc-cyan",
  neutral: "border-white/[0.09] bg-white/[0.04] text-txt-mid",
};
export function Pill({ tone = "neutral", children, className = "" }: { tone?: keyof typeof toneMap; children: React.ReactNode; className?: string }) {
  return <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[10px] font-semibold tracking-wide ${toneMap[tone]} ${className}`}>{children}</span>;
}

export function ChevronSection({ open }: { open: boolean }) {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" className={`text-txt-low transition-transform duration-500 ${open ? "rotate-180" : ""}`}>
      <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* ---------------- connection state ---------------- */
export function ConnectionState({ onRetry, label = "Can't reach your agent" }: { onRetry?: () => void; label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center animate-fadeIn">
      <div className="relative mb-5">
        <span className="absolute inset-0 rounded-full bg-warn/30 blur-xl animate-pulseSoft" style={{ transform: "scale(1.6)" }} />
        <span className="relative flex h-11 w-11 items-center justify-center rounded-full border border-warn/25 bg-warn/[0.07] text-warn">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M2 8.5a15 15 0 0 1 20 0M5.5 12a10 10 0 0 1 13 0M9 15.5a5 5 0 0 1 6 0" />
            <path d="M12 19.2v.1" />
          </svg>
        </span>
      </div>
      <div className="text-[14px] font-medium text-txt-hi">{label}</div>
      <p className="mt-1.5 max-w-[260px] text-[11.5px] leading-relaxed text-txt-faint">
        The server may be waking up or restarting. Retrying automatically — your research data is safe.
      </p>
      {onRetry && (
        <button className="btn-ghost mt-5" onClick={onRetry}>Try now</button>
      )}
    </div>
  );
}
