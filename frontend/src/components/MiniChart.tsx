/**
 * MiniChart - tiny real-data sparkline for signal cards (approved Signals ref).
 * Fetches the same live candles the engine sees (limit 40) once per market per
 * minute (module cache) and draws an SVG polyline. No fake data: if the fetch
 * fails the sparkline simply does not render.
 */
import { useEffect, useState } from "react";
import { api } from "../lib/api";

type Pt = { close: number };
const cache = new Map<string, { at: number; pts: number[] }>();
const TTL = 60_000;

export function useSpark(market: string, limit = 40) {
  const [pts, setPts] = useState<number[] | null>(null);
  useEffect(() => {
    let dead = false;
    const run = async () => {
      const key = `${market}:${limit}`;
      const hit = cache.get(key);
      if (hit && Date.now() - hit.at < TTL) {
        if (!dead) setPts(hit.pts);
        return;
      }
      try {
        const r = await api.get<{ candles?: Pt[] }>(`/api/markets/${market}/candles?tf=15M&limit=${limit}`);
        const next = (r.candles || []).map((c) => c.close);
        if (next.length < 4) return;
        cache.set(key, { at: Date.now(), pts: next });
        if (!dead) setPts(next);
      } catch { /* sparkline stays empty */ }
    };
    run();
    return () => { dead = true; };
  }, [market, limit]);
  const change = pts && pts.length > 1 && pts[0] ? ((pts[pts.length - 1] / pts[0]) - 1) * 100 : null;
  return { pts, change };
}

function pathFor(pts: number[], width: number, height: number, pad = 2) {
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const step = width / (pts.length - 1);
  const y = (v: number) => pad + (height - pad * 2) * (1 - (v - min) / span);
  const line = pts.map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${line} L${width},${height} L0,${height} Z`;
  return { line, area, up: pts[pts.length - 1] >= pts[0] };
}

export function MiniChart({ market, width = 62, height = 40, fill = false }: { market: string; width?: number; height?: number; fill?: boolean }) {
  const { pts } = useSpark(market, fill ? 96 : 40);
  if (!pts || pts.length < 4) return null;
  const { line, area, up } = pathFor(pts, width, height, fill ? 4 : 2);
  const color = up ? "#1fbf75" : "#f0434a";
  const gid = `sp-${market}`;
  return (
    <svg width={fill ? "100%" : width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className={fill ? "block w-full" : "shrink-0"} aria-hidden="true">
      {fill && (
        <>
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.28" />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={area} fill={`url(#${gid})`} />
        </>
      )}
      <path d={line} fill="none" stroke={color} strokeWidth={fill ? 2 : 1.6} strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
