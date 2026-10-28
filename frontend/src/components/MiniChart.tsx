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

async function load(market: string): Promise<number[] | null> {
  const hit = cache.get(market);
  if (hit && Date.now() - hit.at < TTL) return hit.pts;
  try {
    const r = await api.get<{ candles?: Pt[] }>(`/api/markets/${market}/candles?tf=15M&limit=40`);
    const pts = (r.candles || []).map((c) => c.close);
    if (pts.length < 4) return null;
    cache.set(market, { at: Date.now(), pts });
    return pts;
  } catch {
    return null;
  }
}

export function MiniChart({ market, width = 62, height = 40 }: { market: string; width?: number; height?: number }) {
  const [pts, setPts] = useState<number[] | null>(null);
  useEffect(() => {
    let dead = false;
    load(market).then((p) => { if (!dead) setPts(p); });
    return () => { dead = true; };
  }, [market]);

  if (!pts || pts.length < 4) return null;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const step = width / (pts.length - 1);
  const up = pts[pts.length - 1] >= pts[0];
  const color = up ? "#16a34a" : "#e5484d";
  const d = pts
    .map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(height - 3 - ((v - min) / span) * (height - 6)).toFixed(1)}`)
    .join(" ");
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="shrink-0" aria-hidden="true">
      <path d={d} fill="none" stroke={color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" opacity="0.9" />
    </svg>
  );
}
