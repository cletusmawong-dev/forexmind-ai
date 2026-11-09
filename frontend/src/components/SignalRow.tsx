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
  "badge-gain",
  "badge-accent",
  "badge",
  "badge-loss",
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
    return <span className="badge badge-gain">WON</span>;
  if (s.outcome === "LOSS")
    return <span className="badge badge-loss">LOSS</span>;
  if ((s as any).mt5_ticket && !s.completed)
    return <span className="badge badge-accent">IN TRADE</span>;
  if (s.completed)
    return <span className="shrink-0 whitespace-nowrap rounded-full border border-[var(--glass-border)] bg-[var(--bg-secondary)] px-2 py-[3px] text-[8.5px] font-bold tracking-[0.06em] text-[var(--text-muted)]">{statusToneShort(s.status).toUpperCase()}</span>;
  return <span className="badge badge-gain">LIVE</span>;
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
        className="press flex w-full items-center gap-3 border-b border-white/[0.06] px-4 py-3.5 text-left active:bg-white/[0.03]"
        aria-label={`${s.market} ${s.direction} signal${s.extra_signal ? " (extra, not entered)" : ""}`}
      >
        <MarketFlag market={s.market} size={40} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[14.5px] font-bold tracking-tight">{s.market}</span>
            <span className={`badge ${buy ? "badge-gain" : "badge-loss"}`}>
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
                return <span className="text-accent">live · in position</span>;
              const tp = (s.status || "").match(/TP(\d)/);
              if (tp) return <span className="text-gain">TP{tp[1]}: {fmtR(s.r_multiple)}</span>;
              if (s.outcome === "WIN") return <span className="text-gain">{fmtR(s.r_multiple)}</span>;
              if (s.outcome === "LOSS") return <span className="text-loss">{fmtR(s.r_multiple)}</span>;
              if (s.status === "ACTIVE") return <span className="text-accent">score {s.score}</span>;
              return <span className="text-[var(--text-muted)]">{statusToneShort(s.status)}</span>;
            })()}
          </div>
        </div>
        <ChevronRight size={16} className="shrink-0 text-[var(--text-muted)] opacity-70" />
      </button>
    );
  }

  return (
    <button
      onClick={onClick}
      className="press flex w-full items-center gap-3 border-b border-white/[0.06] px-4 py-3.5 text-left active:bg-white/[0.03]"
      aria-label={`${s.market} ${s.direction} signal`}
    >
      <MarketFlag market={s.market} size={40} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-[16px] font-semibold tracking-tight">{s.market}</span>
          <span className={`text-[13px] font-medium ${buy ? "text-pos" : "text-neg"}`}>{s.direction === "BUY" ? "Buy" : "Sell"}</span>
        </div>
        <div className="mt-0.5 truncate text-[13px] text-[var(--text-muted)]">
          {s.strategy_name || "Strategy"} · {s.timeframe}
          {notEntered ? ` · ${notEntered}` : ""}
        </div>
      </div>
      <div className="text-right">
        <div className="num text-[16px] font-medium">{fmtPrice(s.entry)}</div>
        <div className={`num text-[13px] ${s.outcome === "LOSS" ? "text-neg" : s.outcome === "WIN" ? "text-pos" : "text-[var(--text-muted)]"}`}>
          {s.rr_primary ? `1:${Number(s.rr_primary).toFixed(1)}` : s.status?.replaceAll("_", " ").toLowerCase()}
        </div>
      </div>
    </button>
  );
}
