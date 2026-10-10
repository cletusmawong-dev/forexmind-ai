/* FX RULE CANDIDATES panel (owner spec batch 2026-10-10) — RESEARCH ONLY.
 *
 * Reads GET /api/research/candidates/fx: four deterministic XAUUSD rule
 * engines (H1 breakout, opening range, liquidity sweep, CRT 4H+15M)
 * evaluated on live candles. This panel is a WATCHLIST of rule output.
 *
 * It deliberately has NO buttons and NO actions: nothing here can place an
 * order, touch MT5, or flip a strategy. Every response is forced
 * executionEnabled:false on the backend and every card is labelled
 * RESEARCH ONLY. A historical candidate is NOT a fresh backtest and NOT a
 * claim of profitability.
 */
import { FlaskConical, ShieldOff } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import { Divider, ErrorNote, Glass, Pill, SectionHeader, Spinner, TEXT } from "./ui";

type FxStatus = "NO_SIGNAL" | "INSUFFICIENT_DATA" | "RESEARCH_CANDIDATE" | "SKIP_RISK";

interface FxSizing {
  account_equity?: number; risk_pct?: number; risk_budget?: number;
  stop_distance?: number; contract_size?: number; raw_lot?: number;
  lot?: number; note?: string;
}
interface FxCandidate {
  id: string; name: string; version: string; market: string;
  status: FxStatus; reason: string; direction: string | null;
  entry: number | null; sl: number | null; tp: number | null;
  r_multiple: number | null; break_even_marker_r: number | null;
  signal_time: string | null; sizing?: FxSizing;
  executionEnabled: boolean; label: string;
}
interface FxResponse {
  market: string; label: string; generated_at: string;
  executionEnabled: boolean; note: string; candidates: FxCandidate[];
}

function statusPill(s: FxStatus) {
  if (s === "RESEARCH_CANDIDATE")
    return <Pill tone="warn"><FlaskConical size={11} className="mr-1 inline" />Research candidate</Pill>;
  if (s === "SKIP_RISK")
    return <Pill tone="danger">Skip - risk</Pill>;
  if (s === "INSUFFICIENT_DATA")
    return <Pill tone="neutral">Insufficient data</Pill>;
  return <Pill tone="neutral">No signal</Pill>;
}

function dirLabel(d: string | null) {
  if (d === "LONG") return <span className="text-[var(--accent-green)]">LONG</span>;
  if (d === "SHORT") return <span className="text-[var(--accent-red)]">SHORT</span>;
  return <span className={TEXT.faint}>—</span>;
}

const fmt = (v: number | null | undefined, dp = 3) =>
  v == null ? "—" : Number(v).toFixed(dp);

function FxCard({ c }: { c: FxCandidate }) {
  const sz = c.sizing || {};
  return (
    <Glass level={2}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className={`truncate text-[14px] font-bold ${TEXT.headline}`}>{c.name}</p>
          <p className={`mt-0.5 text-[10.5px] ${TEXT.faint}`}>
            {c.id} · v{c.version} · {c.market}
          </p>
        </div>
        {statusPill(c.status)}
      </div>

      <p className={`mt-1.5 text-[11px] leading-snug ${TEXT.hint}`}>{c.reason}</p>

      <Divider />

      <div className="grid grid-cols-3 gap-2">
        <div>
          <p className={`text-[9px] uppercase tracking-[0.12em] ${TEXT.faint}`}>Direction</p>
          <p className="text-[13px] font-semibold">{dirLabel(c.direction)}</p>
        </div>
        <div>
          <p className={`text-[9px] uppercase tracking-[0.12em] ${TEXT.faint}`}>Entry</p>
          <p className={`text-[13px] font-semibold ${TEXT.headline}`}>{fmt(c.entry)}</p>
        </div>
        <div>
          <p className={`text-[9px] uppercase tracking-[0.12em] ${TEXT.faint}`}>Stop</p>
          <p className={`text-[13px] font-semibold ${TEXT.headline}`}>{fmt(c.sl)}</p>
        </div>
        <div>
          <p className={`text-[9px] uppercase tracking-[0.12em] ${TEXT.faint}`}>Target</p>
          <p className={`text-[13px] font-semibold ${TEXT.headline}`}>{fmt(c.tp)}</p>
        </div>
        <div>
          <p className={`text-[9px] uppercase tracking-[0.12em] ${TEXT.faint}`}>R multiple</p>
          <p className={`text-[13px] font-semibold ${TEXT.headline}`}>
            {c.r_multiple == null ? "—" : `${c.r_multiple}R`}
          </p>
        </div>
        <div>
          <p className={`text-[9px] uppercase tracking-[0.12em] ${TEXT.faint}`}>BE marker</p>
          <p className={`text-[13px] font-semibold ${TEXT.headline}`}>
            {c.break_even_marker_r == null ? "none" : `+${c.break_even_marker_r}R`}
          </p>
        </div>
        <div>
          <p className={`text-[9px] uppercase tracking-[0.12em] ${TEXT.faint}`}>Signal time (UTC)</p>
          <p className={`text-[11.5px] font-semibold ${TEXT.body}`}>
            {c.signal_time ? c.signal_time.replace("T", " ").slice(0, 16) : "—"}
          </p>
        </div>
        <div>
          <p className={`text-[9px] uppercase tracking-[0.12em] ${TEXT.faint}`}>Lot (research)</p>
          <p className={`text-[11.5px] font-semibold ${TEXT.body}`}>
            {sz.lot != null ? sz.lot : "—"}
            {sz.risk_budget != null && (
              <span className={`ml-1 text-[9.5px] font-normal ${TEXT.faint}`}>
                @ {fmt(sz.risk_budget, 2)} budget
              </span>
            )}
          </p>
        </div>
      </div>

      <p className={`mt-2.5 flex items-center gap-1 text-[9.5px] uppercase tracking-[0.1em] ${TEXT.faint}`}>
        <ShieldOff size={11} className="shrink-0" />
        Execution disabled · research output only
      </p>
    </Glass>
  );
}

export function FxCandidatesPanel() {
  const fx = usePolling<FxResponse>(() => api.get(endpoints.researchFxCandidates), 60000);
  const cands = fx.data?.candidates || [];

  return (
    <Glass>
      <SectionHeader>FX rule candidates — XAUUSD</SectionHeader>
      <p className={`text-[11px] ${TEXT.hint}`}>{fx.data?.note}</p>
      <p className={`mt-1 text-[9.5px] ${TEXT.faint}`}>
        Deterministic rule engines evaluated on the latest completed candles.
        A historical candidate report is not a fresh backtest and not a claim of
        profitability. These never place orders.
      </p>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Pill tone="warn"><FlaskConical size={11} className="mr-1 inline" />Research only</Pill>
        {fx.data?.executionEnabled === false && (
          <Pill tone="neutral">executionEnabled: false</Pill>
        )}
        {fx.data?.generated_at && (
          <span className={`text-[10px] ${TEXT.faint}`}>
            evaluated {fx.data.generated_at.replace("T", " ").slice(0, 16)} UTC
          </span>
        )}
      </div>

      <Divider />

      {fx.loading && !fx.data ? (
        <Spinner label="Evaluating FX rules…" />
      ) : fx.error ? (
        <ErrorNote message={fx.error || "FX candidates unavailable"} />
      ) : cands.length === 0 ? (
        <p className={`text-[12px] ${TEXT.hint}`}>No FX candidate engines returned.</p>
      ) : (
        <div className="space-y-3">
          {cands.map((c) => <FxCard key={c.id} c={c} />)}
        </div>
      )}
    </Glass>
  );
}
