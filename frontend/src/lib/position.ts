/** MT5 position sizing from signal levels + account risk.
 *  lot = risk$ / (SL distance in pips x pip value per lot). */

interface Spec {
  pipSize: number;
  value: number | ((price: number) => number); // USD per pip per 1.0 lot
  contract: string;
  verify?: boolean;
}

const SPECS: Record<string, Spec> = {
  EURUSD: { pipSize: 0.0001, value: 10, contract: "1 lot = 100,000 EUR" },
  GBPUSD: { pipSize: 0.0001, value: 10, contract: "1 lot = 100,000 GBP" },
  USDJPY: { pipSize: 0.01, value: (p) => 1000 / (p || 1), contract: "1 lot = 100,000 $" },
  XAUUSD: { pipSize: 0.1, value: 10, contract: "1 lot = 100 oz", verify: true },
  NAS100: { pipSize: 1, value: 1, contract: "$1 / point", verify: true },
};

export interface LotResult {
  ok: boolean;
  lots: number;
  rawLots: number;
  pips: number;
  pipValue: number;
  riskUSD: number;
  contract: string;
  note?: string;
}

export function calcLot(
  market: string,
  entry: number,
  sl: number,
  balance: number,
  riskPct: number,
  price?: number
): LotResult {
  const spec = SPECS[market];
  const fail: LotResult = { ok: false, lots: 0, rawLots: 0, pips: 0, pipValue: 0, riskUSD: 0, contract: spec?.contract ?? "" };
  if (!spec || !entry || !sl || !balance || !riskPct) return fail;
  const refPrice = price || entry;
  const pipValue = typeof spec.value === "function" ? spec.value(refPrice) : spec.value;
  const pips = Math.abs(entry - sl) / spec.pipSize;
  const riskUSD = (balance * riskPct) / 100;
  if (pips <= 0 || pipValue <= 0 || riskUSD <= 0) return fail;
  const rawLots = riskUSD / (pips * pipValue);
  const lots = Math.max(0.01, Math.floor(rawLots * 100 + 1e-9) / 100); // MT5 lot step 0.01, round DOWN (epsilon: float-safe)
  return {
    ok: true,
    lots,
    rawLots,
    pips: Math.round(pips * 10) / 10,
    pipValue: Math.round(pipValue * 100) / 100,
    riskUSD: Math.round(riskUSD * 100) / 100,
    contract: spec.contract,
    note: rawLots < 0.01
      ? "Risk budget is below one 0.01 lot - trade the minimum or skip; never exceed your risk %."
      : spec.verify
      ? "Verify this contract size with your MT5 broker - gold/index specs vary."
      : undefined,
  };
}
