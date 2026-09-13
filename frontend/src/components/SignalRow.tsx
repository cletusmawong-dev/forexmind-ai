import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import type { Signal } from "../lib/types";
import { fmtPrice, fmtR, statusToneShort } from "../lib/format";

/** Refined market row used in lists — lightweight, hairline-separated. */
export function SignalRow({ signal: s, onClick }: { signal: Signal; onClick?: () => void }) {
  const buy = s.direction === "BUY";
  const open = !s.completed;
  return (
    <button
      onClick={onClick}
      className="tap group relative flex w-full items-center gap-4 px-5 py-4 text-left transition-colors hover:bg-white/[0.025]"
    >
      {/* direction glow */}
      <span
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border"
        style={{
          borderColor: buy ? "rgba(62,207,142,0.3)" : "rgba(240,120,140,0.3)",
          background: buy ? "rgba(62,207,142,0.08)" : "rgba(240,120,140,0.08)",
          boxShadow: buy ? "0 0 16px rgba(62,207,142,0.12)" : "0 0 16px rgba(240,120,140,0.12)",
        }}
      >
        {buy ? <ArrowUpRight size={15} className="text-pos" /> : <ArrowDownRight size={15} className="text-neg" />}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[14px] font-semibold tracking-tight">{s.market}</span>
          <span className={`text-[10px] font-bold tracking-[0.08em] ${buy ? "text-pos" : "text-neg"}`}>{s.direction}</span>
          <span className="text-[10px] font-medium text-txt-faint">{s.timeframe}</span>
          {s.user_action === "entered" && <span className="h-1 w-1 rounded-full bg-acc-cyan" title="You entered" />}
        </div>
        <div className="mt-0.5 truncate text-[11px] text-txt-low">
          {s.strategy_name} · <span className="text-txt-faint">{s.signal_id}</span>
        </div>
      </div>

      <div className="shrink-0 text-right">
        <div className="num text-[13.5px] font-semibold text-txt-hi">{fmtPrice(s.entry)}</div>
        <div className="mt-0.5 text-[10.5px] font-medium">
          {open ? (
            <span className="text-txt-faint">score {s.score}</span>
          ) : (
            <span
              className={
                s.outcome === "WIN" ? "text-pos" : s.outcome === "LOSS" ? "text-neg" : "text-txt-faint"
              }
            >
              {statusToneShort(s.status)} · {fmtR(s.r_multiple)}
            </span>
          )}
        </div>
      </div>
    </button>
  );
}
