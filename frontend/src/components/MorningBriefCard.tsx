import { useEffect, useState } from "react";
import { Newspaper } from "lucide-react";
import { api } from "../lib/api";
import { Glass, StatusDot } from "./ui";

function minsUntil(iso: string): number {
  return Math.round((Date.parse(iso) - Date.now()) / 60000);
}
function whenLabel(iso: string): string {
  const m = minsUntil(iso);
  if (m >= 0) return m < 60 ? `in ${m}m` : `in ${Math.floor(m / 60)}h ${m % 60}m`;
  return `${-m}m ago`;
}

/** AI Morning Brief (from real data, written by XKiro) + today's red news. */
export function MorningBriefCard() {
  const [brief, setBrief] = useState<string>("");
  const [events, setEvents] = useState<{ title: string; country: string; time: string }[]>([]);
  const [, tick] = useState(0);

  useEffect(() => {
    let alive = true;
    api.get("/api/agent/brief").then((d: any) => alive && setBrief(d.brief || "")).catch(() => {});
    api.get("/api/calendar").then((d: any) => alive && setEvents(d.events || [])).catch(() => {});
    const t = setInterval(() => tick((x) => x + 1), 60000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (!brief && events.length === 0) return null;

  return (
    <Glass pad={false} className="!p-5">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-[13.5px] font-bold text-white">
          <Newspaper size={14} className="text-[#8fb4ff]" /> Morning Brief
        </span>
        <StatusDot tone="blue" size={6} pulse={false} />
      </div>

      {brief && (
        <p className="mt-3 whitespace-pre-wrap text-[12px] leading-relaxed text-[var(--text-secondary)]">
          {brief}
        </p>
      )}

      {events.length > 0 && (
        <div className="mt-4 space-y-2 border-t border-white/[0.06] pt-3.5">
          <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-[var(--text-muted)]">
            High-impact news - next 48h
          </div>
          {events.slice(0, 4).map((e, i) => {
            const m = minsUntil(e.time);
            return (
              <div key={i} className="flex items-center justify-between text-[11.5px]">
                <span className="min-w-0 truncate text-[var(--text-secondary)]">
                  <span className="mr-1.5 font-bold" style={{ color: m >= 0 && m <= 30 ? "var(--accent-red)" : "var(--accent-amber)" }}>
                    {e.country}
                  </span>
                  {e.title}
                </span>
                <span className="num ml-2 shrink-0 font-semibold text-[var(--text-muted)]">{whenLabel(e.time)}</span>
              </div>
            );
          })}
          <p className="pt-1 text-[9.5px] text-[var(--text-muted)]">
            The agent pauses signal generation +/-30 min around these.
          </p>
        </div>
      )}
    </Glass>
  );
}
