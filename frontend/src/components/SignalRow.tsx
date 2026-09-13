import { ArrowDownRight, ArrowUpRight, ChevronRight } from "lucide-react";
import type { Signal } from "../lib/types";
import { fmtPrice, fmtR, statusToneShort } from "../lib/format";

/** Circular glass direction icon with soft glow (SELL = pink ↓, BUY = green ↑). */
export function CircleDirIcon({ dir, size = 42 }: { dir: "BUY" | "SELL"; size?: number }) {
  const buy = dir === "BUY";
  const color = buy ? "var(--accent-green)" : "var(--accent-red)";
  return (
    <span
      className="flex shrink-0 items-center justify-center rounded-full border"
      style={{
        width: size,
        height: size,
        borderColor: buy ? "rgba(47,217,138,0.35)" : "rgba(251,77,106,0.35)",
        background: buy
          ? "linear-gradient(145deg, rgba(47,217,138,0.16), rgba(47,217,138,0.04))"
          : "linear-gradient(145deg, rgba(251,77,106,0.16), rgba(251,77,106,0.04))",
        boxShadow: buy ? "0 0 20px rgba(47,217,138,0.18)" : "0 0 20px rgba(251,77,106,0.18)",
      }}
      aria-hidden="true"
    >
      {buy ? <ArrowUpRight size={size * 0.42} style={{ color }} /> : <ArrowDownRight size={size * 0.42} style={{ color }} />}
    </span>
  );
}

/** Premium signal card matching the reference: direction icon, pair + side +
 *  timeframe, strategy · ID line, price right, result badge. */
export function SignalRow({ signal: s, onClick }: { signal: Signal; onClick?: () => void }) {
  const buy = s.direction === "BUY";
  const open = !s.completed;
  return (
    <button
      onClick={onClick}
      className="glass glass-hover tap flex w-full items-center gap-3.5 p-4 text-left"
      aria-label={`${s.market} ${s.direction} signal`}
    >
      <CircleDirIcon dir={s.direction} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[14px] font-bold tracking-tight">{s.market}</span>
          <span
            className="text-[10px] font-bold tracking-[0.08em]"
            style={{ color: buy ? "var(--accent-green)" : "var(--accent-red)" }}
          >
            {s.direction}
          </span>
          <span className="text-[10px] font-medium text-[var(--text-muted)]">{s.timeframe}</span>
          <span className="h-1 w-1 rounded-full bg-white/20" />
        </div>
        <div className="mt-1 truncate text-[11px] text-[var(--text-muted)]">
          {s.strategy_name} · {s.signal_id}
        </div>
      </div>
      <div className="shrink-0 text-right">
        <div className="num text-[13.5px] font-semibold">{fmtPrice(s.entry)}</div>
        <div className="num mt-1 text-[10.5px] font-semibold">
          {open ? (
            <span className="text-[var(--accent-cyan)]">
              {s.status.startsWith("TP") ? `${s.status.replace("_", "")} · +${(s.r_multiple || 0).toFixed(1)}R` : s.status === "ACTIVE" ? `score ${s.score}` : s.status}
            </span>
          ) : s.outcome === "WIN" ? (
            <span className="text-[var(--accent-green)]">{statusToneShort(s.status)} · {fmtR(s.r_multiple)}</span>
          ) : s.outcome === "LOSS" ? (
            <span className="text-[var(--accent-red)]">{statusToneShort(s.status)} · {fmtR(s.r_multiple)}</span>
          ) : (
            <span className="text-[var(--text-muted)]">{statusToneShort(s.status)}</span>
          )}
        </div>
      </div>
      <ChevronRight size={15} className="shrink-0 text-[var(--text-muted)] opacity-60" />
    </button>
  );
}
