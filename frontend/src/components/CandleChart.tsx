import { useMemo } from "react";
import type { Candle } from "../lib/types";

export interface ChartLine {
  price: number;
  color: string;
  label: string;
  style?: "solid" | "dashed";
  fade?: number;
}

const UP = "#3ECF8E";
const DOWN = "#F0788C";

/** Professional terminal-style candlestick chart:
 *  thin candles, whisper grid, smooth level lines with neutral glass labels,
 *  signal marker at the entry candle, live last-price tag. */
export function CandleChart({
  candles,
  lines = [],
  entry,
  sl,
  tp1,
  markerTime,
  markerDirection,
  height = 300,
  showLastPrice = true,
}: {
  candles: Candle[];
  lines?: ChartLine[];
  entry?: number;
  sl?: number;
  tp1?: number;
  markerTime?: string;
  markerDirection?: "BUY" | "SELL";
  height?: number;
  showLastPrice?: boolean;
}) {
  const W = 380;
  const H = height;
  const axisW = 62;
  const padB = 8;

  const view = useMemo(() => {
    const cs = candles.slice(-100);
    if (cs.length < 2) return null;
    let lo = Math.min(...cs.map((c) => c.low));
    let hi = Math.max(...cs.map((c) => c.high));
    const all = [...lines.map((l) => l.price), ...(showLastPrice ? [cs[cs.length - 1].close] : [])].filter((v) => isFinite(v));
    for (const v of all) {
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }
    const span = hi - lo || hi * 0.01;
    lo -= span * 0.08;
    hi += span * 0.08;
    const x = (i: number) => ((i + 0.5) / cs.length) * (W - axisW - 6);
    const y = (p: number) => 8 + (1 - (p - lo) / (hi - lo)) * (H - padB - 8);
    const cw = Math.max(1.5, ((W - axisW - 6) / cs.length) * 0.55);
    let markerIdx = -1;
    if (markerTime) {
      const mt = markerTime.replace(" ", "T");
      markerIdx = cs.findIndex((c) => c.time.startsWith(mt.slice(0, 16)));
    }
    return { cs, lo, hi, x, y, cw, markerIdx };
  }, [candles, lines, H, markerTime, showLastPrice]);

  if (!view) return <div className="h-56 animate-pulse rounded-3xl bg-[rgba(122,92,34,0.045)]" />;
  const { cs, x, y, cw, lo, hi, markerIdx } = view;
  const last = cs[cs.length - 1];

  const zone = (from: number | undefined, to: number | undefined, fill: string) => {
    if (from === undefined || to === undefined || !isFinite(from) || !isFinite(to)) return null;
    return (
      <rect
        x={0}
        y={Math.min(y(from), y(to))}
        width={W - axisW - 6}
        height={Math.abs(y(to) - y(from))}
        fill={fill}
      />
    );
  };

  const fmtTag = (v: number) =>
    Math.abs(v) >= 1000 ? v.toLocaleString("en-US", { maximumFractionDigits: 1 }) : v.toPrecision(6).replace(/\.?0+$/, "");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ direction: "ltr" }}>
      <defs>
        <linearGradient id="zoneP" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={UP} stopOpacity="0.10" />
          <stop offset="100%" stopColor={UP} stopOpacity="0.015" />
        </linearGradient>
        <linearGradient id="zoneL" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={DOWN} stopOpacity="0.015" />
          <stop offset="100%" stopColor={DOWN} stopOpacity="0.10" />
        </linearGradient>
      </defs>

      {/* whisper grid */}
      {[0.22, 0.5, 0.78].map((f) => (
        <line key={f} x1={0} x2={W - axisW} y1={8 + f * (H - padB - 8)} y2={8 + f * (H - padB - 8)} stroke="#ffffff" strokeOpacity={0.035} />
      ))}

      {/* subtle profit / loss zones */}
      {zone(entry, tp1, "url(#zoneP)")}
      {zone(entry, sl, "url(#zoneL)")}

      {/* candles */}
      {cs.map((c, i) => {
        const up = c.close >= c.open;
        const col = up ? UP : DOWN;
        const yO = y(c.open);
        const yC = y(c.close);
        return (
          <g key={i} opacity={0.92}>
            <line x1={x(i)} x2={x(i)} y1={y(c.high)} y2={y(c.low)} stroke={col} strokeWidth={0.9} opacity={0.55} />
            <rect x={x(i) - cw / 2} y={Math.min(yO, yC)} width={cw} height={Math.max(1.1, Math.abs(yC - yO))} fill={col} rx={cw / 3} />
          </g>
        );
      })}

      {/* signal marker */}
      {markerIdx >= 0 && markerDirection && (
        <g transform={`translate(${x(markerIdx)}, ${markerDirection === "BUY" ? y(cs[markerIdx].low) + 12 : y(cs[markerIdx].high) - 12})`}>
          <circle r={5.5} fill={markerDirection === "BUY" ? UP : DOWN} opacity={0.9} />
          <circle r={9} fill="none" stroke={markerDirection === "BUY" ? UP : DOWN} strokeWidth={1} opacity={0.35} />
          <path
            d={markerDirection === "BUY" ? "M-2.4 1.2 L0 -2.4 L2.4 1.2" : "M-2.4 -1.2 L0 2.4 L2.4 -1.2"}
            stroke="#f7f1e3"
            strokeWidth={1.4}
            fill="none"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </g>
      )}

      {/* level lines */}
      {lines.map((l, i) => {
        if (!isFinite(l.price) || l.price < lo || l.price > hi) return null;
        return (
          <line
            key={i}
            x1={0}
            x2={W - axisW}
            y1={y(l.price)}
            y2={y(l.price)}
            stroke={l.color}
            strokeWidth={1.1}
            strokeDasharray={l.style === "dashed" ? "2 5" : undefined}
            strokeLinecap="round"
            opacity={l.fade ?? 0.85}
          />
        );
      })}

      {/* last price line */}
      {showLastPrice && (
        <line x1={0} x2={W - axisW} y1={y(last.close)} y2={y(last.close)} stroke="#A9B1C2" strokeWidth={0.8} strokeDasharray="1 6" strokeLinecap="round" opacity={0.5} />
      )}

      {/* glass labels */}
      {lines.map((l, i) => {
        if (!isFinite(l.price) || l.price < lo || l.price > hi) return null;
        return (
          <g key={`t${i}`}>
            <rect x={W - axisW + 2} y={y(l.price) - 8} width={axisW - 4} height={16} rx={8} fill="#131826" fillOpacity={0.92} stroke="#ffffff" strokeOpacity={0.07} />
            <circle cx={W - axisW + 11} cy={y(l.price)} r={2} fill={l.color} />
            <text x={W - axisW + 18} y={y(l.price) + 3.2} fontSize={8.8} fontWeight={600} fill="#A9B1C2" style={{ letterSpacing: "0.02em" }}>
              {l.label} - {fmtTag(l.price)}
            </text>
          </g>
        );
      })}
      {showLastPrice && (
        <g>
          <rect x={W - axisW + 2} y={y(last.close) - 8} width={axisW - 4} height={16} rx={8} fill="#1d2433" fillOpacity={0.95} stroke="#ffffff" strokeOpacity={0.09} />
          <text x={W - axisW + 9} y={y(last.close) + 3.4} fontSize={9} fontWeight={700} fill="#F2F5FA">
            {fmtTag(last.close)}
          </text>
        </g>
      )}
    </svg>
  );
}
