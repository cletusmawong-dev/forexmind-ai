import { useMemo, useState } from "react";
import { Calculator } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { Signal } from "../lib/types";
import { calcLot } from "../lib/position";
import { Divider, Glass } from "./ui";
import { fmtPrice } from "../lib/format";

/** Position size card: turns a signal's entry/SL into an MT5 lot size
 *  using the user's balance and risk %. Inputs editable, prefilled. */
export function PositionSizeCard({ signal: s }: { signal: Signal }) {
  const status = usePolling<{ goals: { account_balance: number }; progress: { risk_per_trade_pct: number } }>(
    () => api.get(endpoints.agentStatus), 30000
  );
  const [balOverride, setBal] = useState<number | null>(null);
  const [riskOverride, setRisk] = useState<number | null>(null);

  const balance = balOverride ?? status.data?.goals?.account_balance ?? 0;
  const riskPct = riskOverride ?? status.data?.progress?.risk_per_trade_pct ?? 0;

  const r = useMemo(
    () => calcLot(s.market, s.entry, s.sl, balance, riskPct, s.entry),
    [s.market, s.entry, s.sl, balance, riskPct]
  );

  return (
    <Glass className="mt-4">
      <div className="flex items-center gap-2">
        <Calculator size={14} className="text-[#1b69b8]" />
        <span className="text-[13.5px] font-semibold">MT5 position size</span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <label className="block">
          <span className="mb-1 block text-[9px] font-medium uppercase tracking-[0.14em] text-[var(--text-muted)]">Balance $</span>
          <input
            className="input !px-3.5 !py-2.5 text-[13px]"
            inputMode="decimal"
            value={balance}
            onChange={(e) => setBal(parseFloat(e.target.value) || 0)}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[9px] font-medium uppercase tracking-[0.14em] text-[var(--text-muted)]">Risk %</span>
          <input
            className="input !px-3.5 !py-2.5 text-[13px]"
            inputMode="decimal"
            value={riskPct}
            onChange={(e) => setRisk(parseFloat(e.target.value) || 0)}
          />
        </label>
      </div>

      {r.ok ? (
        <>
          <div className="mt-4 flex items-end justify-between rounded-2xl border border-[rgba(18,155,127,0.25)] bg-[rgba(18,155,127,0.08)] px-4 py-3.5">
            <div>
              <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-[var(--text-muted)]">Trade this size</div>
              <div className="num mt-1 text-[30px] font-extrabold leading-none text-[var(--text-primary)]" style={{ textShadow: "0 0 24px rgba(18,155,127,0.5)" }}>
                {r.lots.toFixed(2)} <span className="text-[14px] font-semibold text-[var(--text-secondary)]">lots</span>
              </div>
            </div>
            <div className="text-right text-[10.5px] leading-relaxed text-[var(--text-secondary)]">
              <div><span className="num font-semibold text-[var(--text-primary)]">{r.pips}</span> pips to SL</div>
              <div>risking <span className="num font-semibold text-[var(--text-primary)]">${r.riskUSD.toFixed(2)}</span></div>
              <div>${r.pipValue.toFixed(2)} / pip / lot</div>
            </div>
          </div>
          <p className="mt-2.5 text-[10px] leading-relaxed text-[var(--text-muted)]">
            {r.contract}. Lots rounded down to the 0.01 step so you never risk more than planned.
            {r.note ? ` [!]  ${r.note}` : ""}
          </p>
        </>
      ) : (
        <p className="mt-3 text-[11.5px] text-[var(--text-muted)]">Enter balance and risk % to compute the lot size.</p>
      )}

      <Divider className="my-3.5" />
      <p className="text-[10px] text-[var(--text-muted)]">
        Entry <span className="num">{fmtPrice(s.entry)}</span> - SL <span className="num">{fmtPrice(s.sl)}</span> - sizing from your own risk rules.
      </p>
    </Glass>
  );
}
