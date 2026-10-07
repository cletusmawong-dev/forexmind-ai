/** Professional chart (master upgrade §4) - TradingView Lightweight Charts
 *  on REAL FOREXMIND market data.
 *
 *  - Candlesticks + volume + server-computed EMA9/EMA21 (no lookahead)
 *  - Timeframes 1M 5M 15M 30M 1H 4H 1D, symbol from the market catalog
 *  - Your own recent signals as markers; entry/SL/TP1-3 horizontal lines
 *  - Strategy 2 liquidity/sweep/structure levels drawn while a setup is live
 *  - "Why no trade?" answers from real system state (§13)
 *  - "Ask AI" carries the exact chart context into the chat (§5)
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  createChart, ColorType, CrosshairMode, LineStyle,
  type IChartApi, type ISeriesApi, type CandlestickData, type IPriceLine,
} from "lightweight-charts";
import { CandlestickChart, Sparkles, ShieldQuestion, RefreshCw } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { Eyebrow, Glass } from "../components/ui";

const TFS = ["1M", "5M", "15M", "30M", "1H", "4H", "1D"] as const;
type Tf = (typeof TFS)[number];

const SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
  "AUDUSD", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "XAGUSD",
  "BTCUSD", "ETHUSD"];

const GOLD = "#d4af37";
const UP = "#26a69a";
const DOWN = "#ef5350";

type Candle = { time: string; open: number; high: number; low: number; close: number; volume?: number };
type Overlay = { time: string; value: number };
type Setup = { state: string; direction: string | null; levels: Record<string, number | null> };
type Annot = {
  signal_id: string; direction: string; entry: number; sl: number;
  tp1?: number; tp2?: number; tp3?: number; status: string;
  outcome?: string; r_multiple?: number; candle_time?: string; createdAt?: string;
};

function toUtc(t: string): number {
  return Math.floor(new Date(t.replace(" ", "T").replace(/\+00:00$/, "Z")).getTime() / 1000);
}

export function ChartsScreen() {
  const [symbol, setSymbol] = useState("XAUUSD");
  const [tf, setTf] = useState<Tf>("15M");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [ov, setOv] = useState<{ ema9: Overlay[]; ema21: Overlay[] }>({ ema9: [], ema21: [] });
  const [setup, setSetup] = useState<Setup | null>(null);
  const [annots, setAnnots] = useState<Annot[]>([]);
  const [whynot, setWhynot] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const boxRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const e9Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const e21Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);

  // create the chart once
  useEffect(() => {
    if (!boxRef.current) return;
    const chart = createChart(boxRef.current, {
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#9aa3b2", fontSize: 11 },
      grid: { vertLines: { color: "rgba(212,175,55,0.05)" }, horzLines: { color: "rgba(212,175,55,0.05)" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "rgba(212,175,55,0.15)" },
      timeScale: { borderColor: "rgba(212,175,55,0.15)", timeVisible: true, secondsVisible: false },
      autoSize: true,
    });
    const candleSeries = chart.addCandlestickSeries({
      upColor: UP, downColor: DOWN, borderVisible: false,
      wickUpColor: UP, wickDownColor: DOWN,
    });
    const volSeries = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" } });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    const e9 = chart.addLineSeries({ color: "#f0c85c", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "EMA9" });
    const e21 = chart.addLineSeries({ color: GOLD, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "EMA21" });
    Object.assign(chartRef, { current: chart });
    candleRef.current = candleSeries; volRef.current = volSeries;
    e9Ref.current = e9; e21Ref.current = e21;
    return () => { chart.remove(); chartRef.current = null; };
  }, []);

  const loadData = async () => {
    setLoading(true); setErr(null); setWhynot(null);
    try {
      const [cd, ovl, st, an] = await Promise.all([
        api.get<{ candles: Candle[] }>(endpoints.candles(symbol, tf, 400)),
        api.get<{ ema9: Overlay[]; ema21: Overlay[] }>(`/api/charts/overlays?symbol=${symbol}&tf=${tf}&limit=400`),
        api.get<Setup>(`/api/charts/setup?symbol=${symbol}&tf=15M`).catch(() => null),
        api.get<{ annotations: Annot[] }>(`/api/charts/annotations?symbol=${symbol}&limit=10`),
      ]);
      setCandles(cd.candles || []);
      setOv({ ema9: ovl.ema9 || [], ema21: ovl.ema21 || [] });
      setSetup(st);
      setAnnots(an.annotations || []);
    } catch (e: any) { setErr(e?.message || "chart data unavailable"); }
    setLoading(false);
  };

  useEffect(() => { loadData(); /* eslint-disable-next-line */ }, [symbol, tf]);

  // feed series + draw lines/markers whenever data changes
  useEffect(() => {
    if (!candleRef.current || !candles.length) return;
    const rows: CandlestickData[] = candles.map((c) => ({
      time: toUtc(c.time) as any, open: c.open, high: c.high, low: c.low, close: c.close,
    }));
    candleRef.current.setData(rows);
    volRef.current?.setData(candles.filter((c) => c.volume != null).map((c) => ({
      time: toUtc(c.time) as any, value: c.volume!,
      color: c.close >= c.open ? "rgba(38,166,154,0.35)" : "rgba(239,83,80,0.35)",
    })) as any);
    const pts = (arr: Overlay[]) => arr.map((p) => ({ time: toUtc(p.time) as any, value: p.value }));
    e9Ref.current?.setData(pts(ov.ema9) as any);
    e21Ref.current?.setData(pts(ov.ema21) as any);

    // entry/SL/TP lines from the most recent signal on this symbol
    for (const l of linesRef.current) { try { candleRef.current?.removePriceLine(l); } catch { /* noop */ } }
    linesRef.current = [];
    const mk = (price: number, color: string, title: string, style = LineStyle.Dashed) => {
      if (!candleRef.current) return;
      linesRef.current.push(candleRef.current.createPriceLine({
        price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title,
      }));
    };
    const last = annots[0];
    if (last) {
      mk(last.entry, "#f0c85c", `ENTRY ${last.direction}`);
      if (last.sl) mk(last.sl, DOWN, "SL");
      if (last.tp1) mk(last.tp1, UP, "TP1", LineStyle.Dotted);
      if (last.tp2) mk(last.tp2, UP, "TP2", LineStyle.Dotted);
      if (last.tp3) mk(last.tp3, UP, "TP3", LineStyle.Dotted);
    }
    // Strategy 2 live levels: liquidity / swept level
    if (setup?.levels) {
      const L = setup.levels;
      if (L.structure_high != null) mk(L.structure_high as number, "rgba(240,200,92,0.8)", "LIQUIDITY ↑", LineStyle.Solid);
      if (L.structure_low != null) mk(L.structure_low as number, "rgba(240,200,92,0.8)", "LIQUIDITY ↓", LineStyle.Solid);
      if (L.swept_level != null) mk(L.swept_level as number, "rgba(160,120,255,0.9)", "SWEPT", LineStyle.LargeDashed);
    }
    chartRef.current?.timeScale().fitContent();
  }, [candles, ov, annots, setup]);

  const askWhy = async () => {
    setWhynot("…");
    try {
      const r = await api.get<{ reason: string; checks: any[] }>(`/api/whynot?symbol=${symbol}`);
      setWhynot(r.reason);
    } catch { setWhynot("could not evaluate right now"); }
  };

  const markers = useMemo(() => annots
    .filter((a) => a.candle_time || a.createdAt)
    .map((a) => ({
      time: toUtc((a.candle_time || a.createdAt)!.replace(" ", "T").replace(/\+00:00$/, "Z")) as any,
      position: a.direction === "BUY" ? ("belowBar" as const) : ("aboveBar" as const),
      color: a.outcome === "WIN" ? UP : a.outcome === "LOSS" ? DOWN : GOLD,
      shape: a.direction === "BUY" ? ("arrowUp" as const) : ("arrowDown" as const),
      text: `${a.status}`,
    })), [annots]);

  useEffect(() => {
    try { candleRef.current?.setMarkers(markers as any); } catch { /* older builds */ }
  }, [markers]);

  return (
    <div className="animate-fadeUp">
      <header className="mb-5 flex items-start justify-between">
        <div className="flex items-center gap-3.5">
          <span className="icon-chip" aria-hidden="true"><CandlestickChart size={20} /></span>
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight">Charts</h1>
            <p className="mt-1 text-[12.5px] text-txt-low">Real market data · EMA9/21 · your signals</p>
          </div>
        </div>
        <button onClick={loadData} className="btn-ghost flex items-center gap-1.5 !px-3 !py-2 text-[11px]">
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} /> Refresh
        </button>
      </header>

      <Glass pad={false} className="overflow-hidden">
        <div className="flex gap-2 overflow-x-auto px-3 pt-3">
          {SYMBOLS.map((s) => (
            <button key={s} onClick={() => setSymbol(s)} aria-pressed={symbol === s}
              className={`tap shrink-0 rounded-xl border px-3 py-1.5 text-[11px] font-bold ${symbol === s ? "chip-on" : "chip-off"}`}>
              {s}
            </button>
          ))}
        </div>
        <div className="flex gap-1.5 px-3 py-3">
          {TFS.map((t) => (
            <button key={t} onClick={() => setTf(t)} aria-pressed={tf === t}
              className={`tap flex-1 rounded-lg border px-1 py-1.5 text-[10.5px] font-bold ${tf === t ? "chip-on" : "chip-off"}`}>
              {t}
            </button>
          ))}
        </div>
        <div ref={boxRef} className="h-[340px] w-full" />
        {setup && setup.state !== "NO_SETUP" && (
          <div className="border-t border-[rgba(var(--warm-rgb),0.08)] px-4 py-3">
            <Eyebrow>Strategy 2 · {symbol}</Eyebrow>
            <p className="mt-1 text-[11.5px] text-txt-mid">
              State <span className="font-bold text-txt-hi">{setup.state}</span>
              {setup.direction && <> · direction <span className="font-bold">{setup.direction}</span></>}
              {" — Liquidity → Sweep → Structure Break → Retest → Entry"}
            </p>
          </div>
        )}
      </Glass>

      {err && <p className="mt-3 text-[11.5px] text-neg">⚠ {err}</p>}

      <div className="mt-4 grid grid-cols-2 gap-2.5">
        <button onClick={askWhy} className="btn-ghost flex items-center justify-center gap-2 !py-3 text-[12px]">
          <ShieldQuestion size={14} /> Why no trade?
        </button>
        <Link to={`/agent?symbol=${symbol}&tf=${tf}`}
          className="btn-primary flex items-center justify-center gap-2 !py-3 text-[12px]">
          <Sparkles size={14} /> Ask AI about this chart
        </Link>
      </div>
      {whynot && whynot !== "…" && (
        <Glass className="mt-3">
          <Eyebrow>Why no trade · {symbol}</Eyebrow>
          <p className="mt-1.5 text-[12px] leading-relaxed text-txt-mid">{whynot}</p>
        </Glass>
      )}

      {annots.length > 0 && (
        <Glass className="mt-4">
          <Eyebrow>Your recent signals · {symbol}</Eyebrow>
          <div className="mt-2 space-y-2">
            {annots.slice(0, 5).map((a) => (
              <Link key={a.signal_id} to={`/signals/${a.signal_id}`}
                className="flex items-center justify-between rounded-xl bg-[rgba(var(--p-rgb),0.05)] px-3 py-2.5">
                <span className="text-[11.5px] font-semibold">
                  <span className={a.direction === "BUY" ? "text-pos" : "text-neg"}>{a.direction}</span>{" "}
                  {a.status}
                </span>
                <span className={`text-[11px] font-bold ${a.outcome === "WIN" ? "text-pos" : a.outcome === "LOSS" ? "text-neg" : "text-txt-mid"}`}>
                  {a.r_multiple != null ? `${a.r_multiple > 0 ? "+" : ""}${a.r_multiple}R` : "—"}
                </span>
              </Link>
            ))}
          </div>
        </Glass>
      )}
    </div>
  );
}
