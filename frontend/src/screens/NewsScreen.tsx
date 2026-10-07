/** Market News & Events (master upgrade §7) - REAL economic releases from
 *  the live ForexFactory calendar feed (the same source that drives the
 *  engine's news blackouts). The AI explains an event from its actual
 *  fields only - never fabricated numbers.
 */
import { useState } from "react";
import { Newspaper, Sparkles, RefreshCw } from "lucide-react";
import { api } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import { Eyebrow, Glass } from "../components/ui";

type Ev = {
  title: string; country: string; date: string; impact: string;
  forecast?: string; previous?: string; affected_markets: string[];
};

const IMPACT_COLOR: Record<string, string> = {
  high: "text-neg", medium: "text-warn", low: "text-txt-mid",
};

export function NewsScreen() {
  const news = usePolling<{ events: Ev[]; count: number; source: string }>(
    () => api.get("/api/news/events"), 300000);
  const [expl, setExpl] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const explain = async (ev: Ev) => {
    const key = ev.title + ev.date;
    setBusy(key);
    try {
      const r = await api.post<{ explanation: string }>("/api/news/explain", {
        title: ev.title, country: ev.country, date: ev.date,
        impact: ev.impact, forecast: ev.forecast, previous: ev.previous,
      });
      setExpl((p) => ({ ...p, [key]: r.explanation }));
    } catch { setExpl((p) => ({ ...p, [key]: "explanation unavailable right now" })); }
    setBusy(null);
  };

  return (
    <div className="animate-fadeUp">
      <header className="mb-5 flex items-start justify-between">
        <div className="flex items-center gap-3.5">
          <span className="icon-chip" aria-hidden="true"><Newspaper size={20} /></span>
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight">News & Events</h1>
            <p className="mt-1 text-[12.5px] text-txt-low">Real calendar · AI explains the risk</p>
          </div>
        </div>
        <button onClick={() => news.refresh()} className="btn-ghost flex items-center gap-1.5 !px-3 !py-2 text-[11px]">
          <RefreshCw size={13} className={news.loading ? "animate-spin" : ""} /> Refresh
        </button>
      </header>

      <div className="space-y-3">
        {(news.data?.events || []).map((ev, i) => {
          const key = ev.title + ev.date;
          return (
            <Glass key={key + i}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[13px] font-bold">{ev.country} · {ev.title}</p>
                  <p className="mt-0.5 text-[10.5px] text-txt-faint">
                    {new Date(ev.date).toLocaleString()} · <span className={`font-bold ${IMPACT_COLOR[ev.impact] || ""}`}>{ev.impact} impact</span>
                  </p>
                  {(ev.forecast || ev.previous) && (
                    <p className="mt-1 text-[11px] text-txt-mid">
                      {ev.forecast ? <>forecast {ev.forecast}</> : null}
                      {ev.previous ? <> · previous {ev.previous}</> : null}
                    </p>
                  )}
                </div>
                <button onClick={() => explain(ev)}
                  className="btn-ghost flex shrink-0 items-center gap-1.5 !px-3 !py-2 text-[11px]">
                  <Sparkles size={12} /> {busy === key ? "…" : "Explain"}
                </button>
              </div>
              {ev.affected_markets.length > 0 && (
                <p className="mt-2 text-[10px] text-txt-faint">May move: {ev.affected_markets.join(", ")}</p>
              )}
              {expl[key] && (
                <p className="mt-2.5 rounded-2xl bg-[rgba(var(--p-rgb),0.06)] p-3 text-[11.5px] leading-relaxed text-txt-mid">
                  {expl[key]}
                </p>
              )}
            </Glass>
          );
        })}
        {news.data && news.data.count === 0 && (
          <Glass><p className="py-6 text-center text-[12px] text-txt-mid">
            Calendar feed not reachable right now - it refreshes automatically.
          </p></Glass>
        )}
        {news.data && (
          <p className="px-1 text-[9.5px] text-txt-faint">Source: {news.data.source}</p>
        )}
      </div>
    </div>
  );
}
