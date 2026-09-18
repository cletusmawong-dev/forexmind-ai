import { ArrowDownRight, ArrowUpRight, ChevronRight } from "lucide-react";
import type { Signal } from "../lib/types";
import { fmtPrice, fmtR, statusToneShort } from "../lib/format";

/** Circular glass direction icon with soft glow (SELL = pink, BUY = green). */
export function CircleDirIcon({ dir, size = 44 }: { dir: "BUY" | "SELL"; size?: number }) {
  const buy = dir === "BUY";
  const color = buy ? "var(--accent-green)" : "var(--accent-red)";
  return (
    <span
      className="flex shrink-0 items-center justify-center rounded-full"
      style={{
        width: size,
        height: size,
        border: `1.5px solid ${buy ? "rgba(var(--p-rgb),0.55)" : "rgba(var(--neg-rgb),0.55)"}`,
        background: buy
          ? "radial-gradient(circle at 35% 30%, rgba(var(--p-rgb),0.22), rgba(var(--p-rgb),0.05) 70%)"
          : "radial-gradient(circle at 35% 30%, rgba(var(--neg-rgb),0.22), rgba(var(--neg-rgb),0.05) 70%)",
        boxShadow: buy ? "0 0 22px rgba(var(--p-rgb),0.3)" : "0 0 22px rgba(var(--neg-rgb),0.3)",
      }}
      aria-hidden="true"
    >
      {buy ? <ArrowUpRight size={size * 0.44} style={{ color }} strokeWidth={2.2} /> : <ArrowDownRight size={size * 0.44} style={{ color }} strokeWidth={2.2} />}
    </span>
  );
}

/** Premium signal card matching the reference.
 *  variant "list" (Signals page): pair line + strategy - ID - age, chevron only.
 *  variant "home": adds bold entry price + live result under it. */
export function SignalRow({ signal: s, variant = "list", onClick }: { signal: Signal; variant?: "list" | "home"; onClick?: () => void }) {
  const buy = s.direction === "BUY";
  const open = !s.completed;
  const age = (() => {
    if (!s.createdAt) return "";
    const m = Math.floor((Date.now() - new Date(s.createdAt).getTime()) / 60000);
    if (!Number.isFinite(m) || m < 0) return "";
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  })();
  return (
    <button
      onClick={onClick}
      className="glass glass-hover tap flex w-full items-center gap-3.5 !py-[18px] p-4 text-left"
      aria-label={`${s.market} ${s.direction} signal`}
    >
      <CircleDirIcon dir={s.direction} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[14.5px] font-bold tracking-tight">{s.market}</span>
          <span
            className="text-[10.5px] font-bold tracking-[0.08em]"
            style={{ color: buy ? "var(--accent-green)" : "var(--accent-red)" }}
          >
            {s.direction}
          </span>
          <span className="text-[10.5px] font-medium text-[var(--text-muted)]">{s.timeframe}</span>
          {s.extra_signal && (
            <span className="rounded-full border border-[var(--accent-amber)] px-1.5 py-0.5 text-[8.5px] font-bold tracking-[0.06em] text-[var(--accent-amber)]">
              EXTRA - MANUAL ONLY
            </span>
          )}
        </div>
        <div className="mt-1.5 truncate text-[11px] text-[var(--text-muted)]">
          {s.strategy_name} - {s.signal_id}{age ? ` - ${age}` : ""}
        </div>
      </div>
      {variant === "home" && (
        <div className="shrink-0 text-right">
          <div className="num text-[15px] font-bold">{fmtPrice(s.entry)}</div>
          <div className="num mt-1 text-[10.5px] font-semibold">
            {(() => {
              const tp = (s.status || "").match(/TP(\d)/);
              if (tp) return <span className="text-[var(--accent-green)]">TP{tp[1]}: {fmtR(s.r_multiple)}</span>;
              if (s.outcome === "WIN") return <span className="text-[var(--accent-green)]">{fmtR(s.r_multiple)}</span>;
              if (s.outcome === "LOSS") return <span className="text-[var(--accent-red)]">{fmtR(s.r_multiple)}</span>;
              if (s.status === "ACTIVE") return <span className="text-[var(--accent-cyan)]">score {s.score}</span>;
              return <span className="text-[var(--text-muted)]">{statusToneShort(s.status)}</span>;
            })()}
          </div>
        </div>
      )}
      <ChevronRight size={16} className="shrink-0 text-[var(--text-muted)] opacity-70" />
    </button>
  );
}
