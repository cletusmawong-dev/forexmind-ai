import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Activity, BrainCircuit, RefreshCw } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import { useAsyncAction } from "../lib/useAsyncAction";
import { ActionButton, Empty, ErrorNote, Glass, PageHeader, Pill, SectionHeader, Spinner } from "../components/ui";
import { shortAgo } from "../lib/format";

type LivePosition = {
  ticket: number; market: string; direction: string; volume?: number;
  entry: number; current: number; sl?: number;
  tp1?: number; tp2?: number; tp3?: number;
  profit_usd: number; r_multiple_now?: number | null;
  signal_id?: string | null; strategy?: string | null;
  last_ai?: { trigger?: string; action?: string; answer?: string;
              confidence_pct?: number; gate?: string; at?: string } | null;
};

/** P15 IA - Positions: broker truth for open trades + AI-manager advisory.
 *  Review is ADVICE ONLY - execution happens solely via the manager tick
 *  and the deterministic risk gate (stated on screen, enforced in backend). */
export function PositionsScreen() {
  const navigate = useNavigate();
  const pos = usePolling<{ positions: LivePosition[]; count: number; mode: string; note?: string }>(
    () => api.get("/api/positions/live"), 8000);
  const rows = pos.data?.positions ?? [];

  return (
    <div className="animate-fadeUp space-y-6">
      <PageHeader title="Positions" sub="Live broker positions - truth, not paper"
        icon={<Activity size={20} />}
        right={
          <button onClick={() => pos.refresh()}
            className="tap flex items-center gap-1.5 rounded-xl border border-[rgba(var(--p-rgb),0.25)] px-3 py-1.5 text-[11.5px] text-txt-mid hover:bg-[rgba(var(--p-rgb),0.08)]">
            <RefreshCw size={13} className={pos.loading ? "animate-spin" : ""} /> Refresh
          </button>
        } />
      <ErrorNote message={pos.error} onDismiss={pos.setData} />

      {pos.loading && !pos.data ? <Spinner label="Reading broker positions..." /> :
        rows.length === 0 ? (
          <Glass className="p-8 text-center">
            <Empty title="No open positions"
              sub={pos.data?.mode === "vps" ? "The bridge reports nothing open right now."
                : `Execution mode is "${pos.data?.mode ?? "unknown"}" - positions appear when the VPS bridge is live.`}
              icon={<Activity size={26} />} />
          </Glass>
        ) : (
          <div className="space-y-3">
            {rows.map((p) => (
              <Glass key={p.ticket} className="p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-[14px] font-semibold">
                      {p.market} <span className={p.direction === "BUY" ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"}>{p.direction}</span>
                      <span className="ml-2 text-[11px] font-normal text-txt-mid">{p.volume} lots · #{p.ticket}</span>
                    </p>
                    <p className="text-[11px] text-txt-mid">
                      {p.strategy ?? "unlinked"} {p.signal_id ? `· ${p.signal_id}` : ""}
                    </p>
                    <p className="mt-1 text-[11px] text-txt-mid">
                      entry {p.entry} → now {p.current}
                      {p.r_multiple_now !== null && p.r_multiple_now !== undefined && (
                        <> · <b className={p.r_multiple_now >= 0 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"}>{p.r_multiple_now > 0 ? "+" : ""}{p.r_multiple_now}R</b></>
                      )}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className={`text-[16px] font-semibold ${p.profit_usd >= 0 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"}`}>
                      {p.profit_usd >= 0 ? "+" : ""}{p.profit_usd.toFixed(2)} USD
                    </p>
                    <p className="text-[10px] text-txt-mid">broker floating</p>
                  </div>
                </div>

                {/* TP ladder */}
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {[p.tp1, p.tp2, p.tp3].map((tp, i) => tp ? (
                    <Pill key={i} tone={tp !== undefined && ((p.direction === "BUY" && p.current >= tp) || (p.direction === "SELL" && p.current <= tp)) ? "pos" : "neutral"}>
                      TP{i + 1} {tp}
                    </Pill>
                  ) : null)}
                  <Pill tone="neutral">SL {p.sl ?? "—"}</Pill>
                </div>

                {/* last AI advisory */}
                <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[rgba(var(--warm-rgb),0.08)] px-3 py-2">
                  {p.last_ai ? (
                    <p className="text-[11px] text-txt-mid">
                      <BrainCircuit size={12} className="mr-1 inline" />
                      last AI: <b className="text-txt-hi">{p.last_ai.answer ?? p.last_ai.action ?? "?"}</b>
                      {p.last_ai.confidence_pct !== undefined && p.last_ai.confidence_pct !== null &&
                        <> · {p.last_ai.confidence_pct}% evidence</>}
                      {p.last_ai.gate && <> · gate {p.last_ai.gate}</>}
                      {p.last_ai.at && <> · {shortAgo(p.last_ai.at)}</>}
                    </p>
                  ) : (
                    <p className="text-[11px] text-txt-mid">No AI review yet for this ticket.</p>
                  )}
                  <BrainReviewButton ticket={p.ticket} />
                </div>
              </Glass>
            ))}
            <p className="px-1 text-[10.5px] leading-relaxed text-txt-mid">
              Reviews are advice only. Orders are executed exclusively by the manager
              tick through the deterministic risk gate - never from this screen.
            </p>
          </div>
        )}
    </div>
  );
}

function BrainReviewButton({ ticket }: { ticket: number }) {
  const [result, setResult] = useState<string | null>(null);
  const act = useAsyncAction();
  return (
    <span className="inline-flex flex-col items-end gap-0.5">
      <ActionButton label="AI review" tone="pos" busy={act.busy}
        onRun={async () => {
          const r = await act.run(() => api.post("/api/aimanager/brain/review", { ticket }), "reviewed");
          const b = r?.brain ?? {};
          const text = `${b.answer ?? "?"}${b.confidence_pct !== undefined ? ` · ${b.confidence_pct}%` : ""}`;
          setResult(text);
          return text;
        }} />
      {result && <span className="text-[10px] text-txt-mid">{result}</span>}
    </span>
  );
}

