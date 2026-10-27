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
import { Link, useLocation } from "react-router-dom";
import {
  createChart, ColorType, CrosshairMode, LineStyle,
  type IChartApi, type ISeriesApi, type CandlestickData, type IPriceLine,
} from "lightweight-charts";
import { Sparkles, ShieldQuestion, RefreshCw } from "lucide-react";
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
type Setup = {
  state: string; direction: string | null;
  levels: { zone_type?: string; zone_high?: number | null; zone_low?: number | null;
            fvg_high?: number | null; fvg_low?: number | null; fvg_mid?: number | null };
};
type S2Live = {
  state: string; direction: string; entry: number; sl: number; tps: number[];
  zone_type: string; zone_high: number; zone_low: number;
  fvg_high: number; fvg_low: number; fvg_mid: number; entry_method: string;
};
type S2Zone = {
  kind: string; type: string; direction: string;
  zone_high: number; zone_low: number; zone_from: string; zone_to: string; zone_active: boolean;
  fvg_high: number; fvg_low: number; fvg_from: string; fvg_to: string; fvg_active: boolean;
};
type Overlays = { ema9: Overlay[]; ema21: Overlay[];
                  s2?: { enabled: boolean; live: S2Live | null; zones: S2Zone[] } };
type Annot = {
  signal_id: string; direction: string; entry: number; sl: number;
  tp1?: number; tp2?: number; tp3?: number; status: string;
  outcome?: string; r_multiple?: number; candle_time?: string; createdAt?: string;
};

function toUtc(t: string): number {
  return Math.floor(new Date(t.replace(" ", "T").replace(/\+00:00$/, "Z")).getTime() / 1000);
}

export function ChartsScreen() {
  const location = useLocation();
  const [symbol, setSymbol] = useState(
    () => new URLSearchParams(location.search).get("symbol")?.toUpperCase() || "XAUUSD");
  const [tf, setTf] = useState<Tf>("15M");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [ov, setOv] = useState<{ ema9: Overlay[]; ema21: Overlay[] }>({ ema9: [], ema21: [] });
  const [setup, setSetup] = useState<Setup | null>(null);
  const [s2o, setS2o] = useState<Overlays["s2"]>({ enabled: false, live: null, zones: [] });
  const [showZones, setShowZones] = useState(true);
  const [showFvg, setShowFvg] = useState(true);
  const [showLive, setShowLive] = useState(true);
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
    /* 2026-10-08 contrast audit: axis/legend text was hard-coded #9aa3b2
       (~2.8:1 on the light theme). Read the THEME TOKEN instead so labels
       follow whichever theme is active (>=4.5:1 everywhere). */
    const _txtRgb = getComputedStyle(document.documentElement)
      .getPropertyValue("--txt-low-rgb").trim() || "122,132,150";
    const chart = createChart(boxRef.current, {
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: `rgb(${_txtRgb})`, fontSize: 11 },
      grid: { vertLines: { color: "#f2f4f7" }, horzLines: { color: "#f2f4f7" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#e7e9ee" },
      timeScale: { borderColor: "#e7e9ee", timeVisible: true, secondsVisible: false },
      autoSize: true,
    });
    const candleSeries = chart.addCandlestickSeries({
      upColor: UP, downColor: DOWN, borderVisible: false,
      wickUpColor: UP, wickDownColor: DOWN,
    });
    const volSeries = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" } });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    const e9 = chart.addLineSeries({ color: "#eab308", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "EMA9" });
    const e21 = chart.addLineSeries({ color: "#2563eb", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "EMA21" });
    Object.assign(chartRef, { current: chart });
    candleRef.current = candleSeries; volRef.current = volSeries;
    e9Ref.current = e9; e21Ref.current = e21;
    return () => { chart.remove(); chartRef.current = null; };
  }, []);

  const loadData = async () => {
    setLoading(true); setErr(null); setWhynot(null);
    try {
      const s2tf = tf === "15M" || tf === "30M" || tf === "1H";
      const [cd, ovl, st, an] = await Promise.all([
        api.get<{ candles: Candle[] }>(endpoints.candles(symbol, tf, 400)),
        api.get<Overlays>(`/api/charts/overlays?symbol=${symbol}&tf=${tf}&limit=400`),
        s2tf
          ? api.get<Setup>(`/api/charts/setup?symbol=${symbol}&tf=${tf}`).catch(() => null)
          : Promise.resolve(null),
        api.get<{ annotations: Annot[] }>(`/api/charts/annotations?symbol=${symbol}&limit=10`),
      ]);
      setCandles(cd.candles || []);
      setOv({ ema9: ovl.ema9 || [], ema21: ovl.ema21 || [] });
      setS2o(ovl.s2 || { enabled: false, live: null, zones: [] });
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
    // S2 Supply & Demand + FVG overlays (all layers toggleable)
    const live = s2o?.live;
    if (live) {
      if (showZones) {
        mk(live.zone_high, "rgba(38,166,154,0.55)", `${live.zone_type || "ZONE"} HIGH`, LineStyle.Solid);
        mk(live.zone_low, "rgba(38,166,154,0.55)", `${live.zone_type || "ZONE"} LOW`, LineStyle.Solid);
      }
      if (showFvg) {
        mk(live.fvg_high, "rgba(160,120,255,0.65)", "FVG HIGH", LineStyle.Dotted);
        mk(live.fvg_low, "rgba(160,120,255,0.65)", "FVG LOW", LineStyle.Dotted);
      }
      if (showLive) {
        mk(live.entry, "#f0c85c", `S2 ENTRY ${live.direction} (${live.entry_method})`);
        mk(live.sl, DOWN, "S2 SL");
        if (live.tps[0]) mk(live.tps[0], UP, "S2 TP1", LineStyle.Dotted);
        if (live.tps[1]) mk(live.tps[1], UP, "S2 TP2", LineStyle.Dotted);
        if (live.tps[2]) mk(live.tps[2], UP, "S2 TP3", LineStyle.Dotted);
      }
    }
    if (showZones && s2o?.zones?.length) {
      // most recent still-active historical zones (max 3 - keeps the chart readable)
      const act = s2o.zones.filter((z) => z.zone_active).slice(-3);
      for (const z of act) {
        const tone = z.type === "demand" ? "rgba(38,166,154,0.4)" : "rgba(239,83,80,0.4)";
        mk(z.zone_high, tone, `${z.type.toUpperCase()} ↑`, LineStyle.LargeDashed);
        mk(z.zone_low, tone, `${z.type.toUpperCase()} ↓`, LineStyle.LargeDashed);
      }
    }
    chartRef.current?.timeScale().fitContent();
  }, [candles, ov, annots, setup, s2o, showZones, showFvg, showLive]);

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

  const last = candles.length ? candles[candles.length - 1] : null;
  const first = candles.length ? candles[0] : null;
  const lastEma9 = ov.ema9.length ? ov.ema9[ov.ema9.length - 1].value : null;
  const lastEma21 = ov.ema21.length ? ov.ema21[ov.ema21.length - 1].value : null;
  const fmt = (v: number | null | undefined, dp = 2) =>
    v == null ? "—" : v >= 1000 ? v.toLocaleString("en-US", { maximumFractionDigits: 2 }) : v.toFixed(dp);
  const up = !!(last && first) && last.close >= first.close;

  return (
    <div className="animate-fadeUp">
      {/* symbol selector - reference-style chips */}
      <div className="-mx-4 mb-3 flex gap-2 overflow-x-auto px-4 pb-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {SYMBOLS.map((sym) => (
          <button key={sym} onClick={() => setSymbol(sym)} aria-pressed={symbol === sym}
            className={`tap shrink-0 rounded-full border px-3.5 py-2 text-[12px] font-bold ${symbol === sym ? "chip-on" : "chip-off"}`}>
            {sym}
          </button>
        ))}
      </div>

      {/* price strip - real OHLC from the loaded series */}
      <section className="glass mb-3 !rounded-2xl px-4 py-3.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-[15px] font-extrabold tracking-tight">{symbol}</div>
            <div className="text-[10.5px] text-[var(--text-muted)]">
              {tf} candles · {candles.length || 0} bars · live feed
            </div>
          </div>
          <span className="flex shrink-0 items-center gap-1.5 rounded-full border border-[rgba(18,160,92,0.3)] bg-[rgba(18,160,92,0.08)] px-2.5 py-1 text-[10px] font-bold text-[var(--accent-green)]">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent-green)]" /> LIVE
          </span>
        </div>
        <div className="mt-2 flex items-end justify-between gap-3">
          <div className="num text-[30px] font-extrabold leading-none tracking-tight">
            {last ? fmt(last.close) : "—"}
          </div>
          <div className={`num text-right text-[12px] font-bold ${up ? "text-pos" : "text-neg"}`}>
            {last && first && (
              <>
                {up ? "+" : ""}{fmt(last.close - first.close)}{" "}
                ({up ? "+" : ""}{first.close ? (((last.close - first.close) / first.close) * 100).toFixed(2) : "0.00"}%)
              </>
            )}
          </div>
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2 border-t border-[var(--glass-border-soft)] pt-2.5 text-center">
          {[["High", last?.high], ["Low", last?.low], ["Volume", last?.volume]].map(([k, v]) => (
            <div key={k as string}>
              <div className="text-[9.5px] font-semibold uppercase tracking-[0.1em] text-txt-faint">{k as string}</div>
              <div className="num mt-0.5 text-[12.5px] font-bold text-[var(--text-secondary)]">
                {v == null ? "—" : fmt(v as number, k === "Volume" ? 0 : 2)}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* timeframes */}
      <div className="mb-2 flex gap-1.5">
        {TFS.map((t) => (
          <button key={t} onClick={() => setTf(t)} aria-pressed={tf === t}
            className={`tap flex-1 rounded-full border px-1 py-2 text-[11.5px] font-bold ${tf === t ? "chip-on" : "chip-off"}`}>
            {t}
          </button>
        ))}
      </div>

      {/* chart card */}
      <Glass pad={false} className="overflow-hidden !rounded-2xl">
        <div className="flex items-center justify-between gap-2 px-3.5 pt-3">
          <div className="flex items-center gap-3 text-[10.5px] font-bold">
            <span className="text-[var(--text-secondary)]">EMA 9 <span className="num text-[#ca9a06]">{lastEma9 ? fmt(lastEma9, lastEma9 >= 1000 ? 2 : 3) : "—"}</span></span>
            <span className="text-[var(--text-secondary)]">EMA 21 <span className="num text-[#2563eb]">{lastEma21 ? fmt(lastEma21, lastEma21 >= 1000 ? 2 : 3) : "—"}</span></span>
          </div>
          <button onClick={loadData} aria-label="Refresh chart"
            className="tap flex h-7 w-7 items-center justify-center rounded-full border border-[var(--glass-border)] bg-white text-[var(--text-secondary)]">
            <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
        {(s2o?.enabled || (s2o?.zones?.length ?? 0) > 0 || s2o?.live) && (
          <div className="flex flex-wrap items-center gap-1.5 px-3.5 pb-2">
            {([["S/D zones", showZones, setShowZones], ["FVG", showFvg, setShowFvg],
               ["Entry/SL/TP", showLive, setShowLive]] as const).map(([label, on, set]) => (
              <button key={label} onClick={() => set(!on)} aria-pressed={on}
                className={`tap rounded-full border px-2.5 py-1 text-[9.5px] font-bold ${on ? "chip-on" : "chip-off"}`}>
                {label}
              </button>
            ))}
          </div>
        )}
        <div ref={boxRef} className="h-[360px] w-full" />
        {loading && !candles.length && (
          <p className="px-4 pb-4 text-center text-[11.5px] text-txt-faint">Loading {symbol} candles…</p>
        )}
        {setup && setup.state !== "NO_SETUP" && (
          <div className="border-t border-[var(--glass-border-soft)] px-4 py-3">
            <Eyebrow>Strategy 2 · Supply & Demand + FVG · {symbol} · 15M</Eyebrow>
            <p className="mt-1 text-[11.5px] text-txt-mid">
              State <span className="font-bold text-txt-hi">{setup.state}</span>
              {setup.direction && <> · <span className={`font-bold ${setup.direction === "BUY" ? "text-pos" : "text-neg"}`}>{setup.direction}</span></>}
              {" — Demand/Supply → Displacement → FVG → Retest → Entry"}
            </p>
            {setup.levels?.zone_high != null && (
              <p className="mt-1 text-[10.5px] text-txt-faint">
                {String(setup.levels.zone_type || "zone").toUpperCase()} {Number(setup.levels.zone_low)?.toFixed(5)}–{Number(setup.levels.zone_high)?.toFixed(5)}
                {setup.levels.fvg_high != null && <> · FVG {Number(setup.levels.fvg_low)?.toFixed(5)}–{Number(setup.levels.fvg_high)?.toFixed(5)}</>}
              </p>
            )}
          </div>
        )}
      </Glass>

      {err && <p className="mt-3 text-[11.5px] font-semibold text-neg">{err}</p>}

      <div className="mt-3 grid grid-cols-2 gap-2.5">
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
            {annots.slice(0, 3).map((a) => (
              <Link key={a.signal_id} to={`/signals/${a.signal_id}`}
                className="flex items-center justify-between rounded-xl border border-[var(--glass-border-soft)] bg-[var(--bg-secondary)] px-3 py-2.5">
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
