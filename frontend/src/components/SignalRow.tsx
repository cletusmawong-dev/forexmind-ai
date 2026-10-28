/** Signal card - approved light reference (2026-10-27 Signals screenshot).
 *  Flag icon | market + direction badge + TF + status | age
 *  Entry / SL / TP1 / TP2 levels row
 *  strategy + signal id + strategy tag | real mini sparkline | chevron.
 *  variant "home" keeps the compact hero-row format used on HomeScreen. */
import { ArrowDownRight, ArrowUpRight, ChevronRight } from "lucide-react";
import type { Signal } from "../lib/types";
import { notEnteredReason } from "../lib/execution";
import { fmtPrice, fmtR, statusToneShort } from "../lib/format";
import { MarketFlag } from "./MarketFlag";
import { MiniChart } from "./MiniChart";

const TAG_TONES = [
  "bg-[rgba(18,160,92,0.10)] text-[#0d8a4e]",
  "bg-[rgba(37,99,235,0.10)] text-[#2563eb]",
  "bg-[rgba(184,145,47,0.12)] text-[#96742a]",
  "bg-[rgba(109,90,224,0.10)] text-[#5b4bc4]",
];

export function CircleDirIcon({ dir, size = 44 }: { dir: "BUY" | "SELL"; size?: number }) {
  const buy = dir === "BUY";
  const color = buy ? "var(--accent-green)" : "var(--accent-red)";
  return (
    <span
      className="flex shrink-0 items-center justify-center rounded-full"
      style={{
        width: size,
        height: size,
        border: `1.5px solid ${buy ? "rgba(var(--pos-rgb),0.45)" : "rgba(var(--neg-rgb),0.45)"}`,
        background: buy ? "rgba(var(--pos-rgb),0.1)" : "rgba(var(--neg-rgb),0.1)",
      }}
      aria-hidden="true"
    >
      {buy ? <ArrowUpRight size={size * 0.44} style={{ color }} strokeWidth={2.2} /> : <ArrowDownRight size={size * 0.44} style={{ color }} strokeWidth={2.2} />}
    </span>
  );
}

function StatusPill({ s }: { s: Signal }) {
  const notEntered = notEnteredReason(s);
  if (s.extra_signal)
    return <span className="shrink-0 whitespace-nowrap rounded-full border border-[rgba(217,119,6,0.4)] bg-[rgba(217,119,6,0.08)] px-2 py-[3px] text-[8.5px] font-bold tracking-[0.06em] text-[var(--accent-amber)]">NOT ENTERED</span>;
  if (notEntered)
    return <span className="shrink-0 whitespace-nowrap rounded-full border border-[var(--glass-border)] bg-[var(--bg-secondary)] px-2 py-[3px] text-[8.5px] font-bold tracking-[0.06em] text-[var(--text-muted)]">NOT ENTERED</span>;
  if (s.outcome === "WIN")
    return <span className="shrink-0 whitespace-nowrap rounded-full border border-[rgba(18,160,92,0.35)] bg-[rgba(18,160,92,0.08)] px-2 py-[3px] text-[8.5px] font-bold tracking-[0.06em] text-[#0d8a4e]">WON</span>;
  if (s.outcome === "LOSS")
    return <span className="shrink-0 whitespace-nowrap rounded-full border border-[rgba(229,72,77,0.35)] bg-[rgba(229,72,77,0.07)] px-2 py-[3px] text-[8.5px] font-bold tracking-[0.06em] text-[#c33d42]">LOSS</span>;
  if ((s as any).mt5_ticket && !s.completed)
    return <span className="shrink-0 whitespace-nowrap rounded-full border border-[rgba(37,99,235,0.35)] bg-[rgba(37,99,235,0.08)] px-2 py-[3px] text-[8.5px] font-bold tracking-[0.06em] text-[#2563eb]">IN TRADE</span>;
  if (s.completed)
    return <span className="shrink-0 whitespace-nowrap rounded-full border border-[var(--glass-border)] bg-[var(--bg-secondary)] px-2 py-[3px] text-[8.5px] font-bold tracking-[0.06em] text-[var(--text-muted)]">{statusToneShort(s.status).toUpperCase()}</span>;
  return <span className="shrink-0 whitespace-nowrap rounded-full border border-[rgba(18,160,92,0.35)] bg-[rgba(18,160,92,0.08)] px-2 py-[3px] text-[8.5px] font-bold tracking-[0.06em] text-[#0d8a4e]">LIVE</span>;
}

export function SignalRow({ signal: s, variant = "list", onClick }: { signal: Signal; variant?: "list" | "home"; onClick?: () => void }) {
  const buy = s.direction === "BUY";
  const notEntered = notEnteredReason(s);
  const age = (() => {
    if (!s.createdAt) return "";
    const m = Math.floor((Date.now() - new Date(s.createdAt).getTime()) / 60000);
    if (!Number.isFinite(m) || m < 0) return "";
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  })();

  if (variant === "home") {
    return (
      <button
        onClick={onClick}
        className={`glass glass-hover tap flex w-full items-center gap-3.5 !py-[16px] p-4 text-left ${s.extra_signal ? "border-l-[3px] border-l-[var(--accent-amber)]" : ""}`}
        aria-label={`${s.market} ${s.direction} signal${s.extra_signal ? " (extra, not entered)" : ""}`}
      >
        <MarketFlag market={s.market} size={40} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[14.5px] font-bold tracking-tight">{s.market}</span>
            <span className={`rounded-md px-1.5 py-[2px] text-[9px] font-extrabold tracking-[0.04em] ${buy ? "bg-[rgba(18,160,92,0.12)] text-[#0d8a4e]" : "bg-[rgba(229,72,77,0.10)] text-[#c33d42]"}`}>
              {s.direction}
            </span>
            <span className="text-[10.5px] font-medium text-[var(--text-muted)]">{s.timeframe}</span>
          </div>
          <div className="mt-1 truncate text-[11px] text-[var(--text-muted)]">
            {s.strategy_name} - {s.signal_id}
          </div>
        </div>
        <div className="shrink-0 text-right">
          {!notEntered && <div className="num text-[15px] font-bold">{fmtPrice(s.entry)}</div>}
          {notEntered && <div className="text-[11px] font-bold text-[var(--accent-amber)]">NOT ENTERED</div>}
          <div className="num mt-1 text-[10.5px] font-semibold">
            {(() => {
              if (s.extra_signal) return <span className="text-[var(--accent-amber)]">advisory</span>;
              if (notEntered) return <span className="text-[var(--accent-amber)]">not entered</span>;
              if ((s as any).mt5_ticket && !s.completed)
                return <span className="text-[#2563eb]">live · in position</span>;
              const tp = (s.status || "").match(/TP(\d)/);
              if (tp) return <span className="text-[#0d8a4e]">TP{tp[1]}: {fmtR(s.r_multiple)}</span>;
              if (s.outcome === "WIN") return <span className="text-[#0d8a4e]">{fmtR(s.r_multiple)}</span>;
              if (s.outcome === "LOSS") return <span className="text-[#c33d42]">{fmtR(s.r_multiple)}</span>;
              if (s.status === "ACTIVE") return <span className="text-[#2563eb]">score {s.score}</span>;
              return <span className="text-[var(--text-muted)]">{statusToneShort(s.status)}</span>;
            })()}
          </div>
        </div>
        <ChevronRight size={16} className="shrink-0 text-[var(--text-muted)] opacity-70" />
      </button>
    );
  }

  // ---- full reference card (Signals screen) ----
  const levels: [string, number | null | undefined, string][] = [
    ["Entry", s.entry, "#101828"],
    ["SL", s.sl, "#c33d42"],
    ["TP1", s.tp1, "#0d8a4e"],
    ["TP2", s.tp2, "#0d8a4e"],
  ];
  const tagTone = TAG_TONES[Math.abs([...(s.strategy_id || s.strategy_name || "s")].reduce((a, c) => a + c.charCodeAt(0), 0)) % TAG_TONES.length];

  return (
    <button
      onClick={onClick}
      className={`glass glass-hover tap w-full !rounded-2xl p-3.5 text-left ${s.extra_signal ? "border-l-[3px] border-l-[var(--accent-amber)]" : ""}`}
      aria-label={`${s.market} ${s.direction} signal`}
    >
      <div className="flex items-start gap-3">
        <MarketFlag market={s.market} size={38} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-[15px] font-extrabold tracking-tight">{s.market}</span>
            <span className={`flex items-center gap-1 rounded-md px-1.5 py-[2px] text-[9.5px] font-extrabold ${buy ? "bg-[rgba(18,160,92,0.12)] text-[#0d8a4e]" : "bg-[rgba(229,72,77,0.10)] text-[#c33d42]"}`}>
              <span className={`h-[5px] w-[5px] rounded-full ${buy ? "bg-[#12a05c]" : "bg-[#e5484d]"}`} />
              {s.direction}
            </span>
            <span className="rounded-md bg-[var(--bg-secondary)] px-1.5 py-[2px] text-[9.5px] font-bold text-[var(--text-muted)]">{s.timeframe}</span>
            <StatusPill s={s} />
          </div>
          <div className="mt-1.5 grid grid-cols-4 gap-1">
            {levels.map(([label, val, color]) => (
              <div key={label} className="min-w-0">
                <div className="text-[8.5px] font-bold uppercase tracking-[0.08em]" style={{ color: label === "Entry" ? "#0d8a4e" : label === "SL" ? "#c33d42" : "var(--text-faint)" }}>
                  {label}
                </div>
                <div className="num whitespace-nowrap text-[10.5px] font-bold tracking-[-0.02em]" style={{ color: val == null ? "var(--text-faint)" : color }}>
                  {val == null ? "—" : fmtPrice(val)}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {age && <span className="text-[9.5px] font-medium text-[var(--text-faint)]">{age}</span>}
          <MiniChart market={s.market} width={54} />
        </div>
      </div>
      <div className="mt-2.5 flex items-center justify-between gap-2 border-t border-[var(--glass-border-soft)] pt-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-[10px] text-[var(--text-muted)]">
            {s.signal_id}
          </span>
          <span className={`shrink-0 rounded-md px-1.5 py-[2px] text-[9px] font-bold ${tagTone}`}>
            {s.strategy_name || "Strategy"}
          </span>
          {notEntered && <span className="shrink-0 text-[9.5px] font-semibold text-[var(--accent-amber)]">· {notEntered}</span>}
        </div>
        <ChevronRight size={15} className="shrink-0 text-[var(--text-muted)] opacity-60" />
      </div>
    </button>
  );
}
