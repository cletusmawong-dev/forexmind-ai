import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Crosshair, SlidersHorizontal } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { Signal } from "../lib/types";
import { DemoTag, Empty, Eyebrow, Segmented, Spinner } from "../components/ui";
import { SignalRow } from "../components/SignalRow";

export function SignalsScreen() {
  const [filter, setFilter] = useState("open");
  const [market, setMarket] = useState("all");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const navigate = useNavigate();

  const qs = useMemo(() => {
    const p = new URLSearchParams({ status: filter, limit: "60" });
    if (market !== "all") p.set("market", market);
    return p.toString();
  }, [filter, market]);

  const { data, loading } = usePolling<{ signals: Signal[]; count: number }>(() => api.get(`${endpoints.signals}?${qs}`), 5000, [filter, market]);
  const marketsQ = usePolling<{ markets: { symbol: string }[] }>(() => api.get(endpoints.markets), 60000);
  const riskQ = usePolling<any>(() => api.get(endpoints.settings), 15000);
  const stratQ = usePolling<any>(() => api.get(endpoints.strategies), 15000);

  const signals = data?.signals ?? [];
  const wins = signals.filter((s) => s.outcome === "WIN").length;
  const losses = signals.filter((s) => s.outcome === "LOSS").length;

  /* --- active settings gates (what the engine enforces on NEW signals) --- */
  const tfs: string[] = riskQ.data?.signal_timeframes ?? ["15M"];
  const stratList: any[] = stratQ.data?.strategies ?? [];
  const paused = stratList.filter((s) => s.status !== "ACTIVE");
  const gateLine = [
    `New signals: ${tfs.join(" + ")} only`,
    ...paused.map((s) => `${s.short_name}: OFF`),
  ].join(" - ");

  /* older-than-settings signals in this list (they finish tracking by design) */
  const oldestAllowed = Math.min(...tfs.map((tf) => ({ "5M": 5, "15M": 15, "1H": 60, "4H": 240, "1D": 1440 } as Record<string, number>)[tf] ?? 15));
  const stale = signals.filter((s) => {
    if (!s.createdAt || paused.some((p) => p.id === s.strategy_id)) return true;
    const ageMin = (Date.now() - new Date(s.createdAt).getTime()) / 60000;
    return ageMin > oldestAllowed * 8; // generous window - only flag clearly-older entries
  });
  const showNote = filter !== "closed" && stale.length > 0 && (riskQ.data || stratQ.data);

  return (
    <div className="anim-fadeUp">
      <header className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-[30px] font-extrabold leading-[1.1] tracking-[-0.03em]">Signals</h1>
          <p className="mt-1 text-[13px] text-[var(--text-muted)]">AI-powered trade ideas with full analysis</p>
        </div>
        <div className="mt-1.5 shrink-0"><DemoTag /></div>
      </header>

      {/* live settings gates - exactly what the engine enforces on new signals */}
      {(riskQ.data || stratQ.data) && (
        <div className="glass-2 mb-4 flex items-center gap-2.5 px-4 py-2.5 text-[11px] font-medium text-[var(--text-secondary)]" role="status">
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--accent-green)]" />
          {gateLine}
        </div>
      )}
      {showNote && (
        <div className="mb-4 flex items-start gap-2.5 rounded-2xl border border-[rgba(var(--amber-rgb),0.28)] bg-[rgba(var(--amber-rgb),0.07)] px-4 py-3 text-[11px] leading-relaxed text-[var(--c-golddeep)]">
          <span aria-hidden="true">i</span>
          <span>
            {stale.length} signal{stale.length === 1 ? "" : "s"} here {stale.length === 1 ? "is" : "are"} older than your current settings. They stay until they hit TP/SL - no new ones like them will be created.
          </span>
        </div>
      )}

      <div className="no-scrollbar -mx-4 mb-2.5 flex items-center gap-2 overflow-x-auto px-4 pb-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <button onClick={() => setMarket("all")} aria-pressed={market === "all"}
          className={`tap shrink-0 rounded-full px-4 py-2 text-[12px] font-bold ${market === "all" ? "bg-[#101828] text-white" : "chip-off"}`}>
          All
        </button>
        {(marketsQ.data?.markets ?? []).map((m) => (
          <button key={m.symbol} onClick={() => setMarket(m.symbol)} aria-pressed={market === m.symbol}
            className={`tap shrink-0 rounded-full border px-4 py-2 text-[12px] font-bold ${market === m.symbol ? "bg-[#101828] text-white border-transparent" : "chip-off"}`}>
            {m.symbol}
          </button>
        ))}
        <button onClick={() => setFiltersOpen((o) => !o)} aria-expanded={filtersOpen}
          className={`tap ml-auto flex shrink-0 items-center gap-1.5 rounded-full border px-3.5 py-2 text-[12px] font-bold ${filtersOpen ? "border-transparent bg-[#101828] text-white" : "chip-off"}`}>
          <SlidersHorizontal size={13} /> Filters
        </button>
      </div>

      {filtersOpen && (
        <div className="glass-2 mb-3 !rounded-2xl px-3.5 py-3">
          <Eyebrow>Status</Eyebrow>
          <Segmented
            className="mb-1 mt-1.5"
            value={filter}
            onChange={setFilter}
            options={[
              { key: "open", label: "Open" },
              { key: "closed", label: "Closed" },
              { key: "all", label: "All" },
            ]}
          />
        </div>
      )}

      {filter === "closed" && wins + losses > 0 && (
        <p className="mb-4 px-1 text-[12px] text-[var(--text-secondary)]">
          <span className="num font-semibold text-[var(--accent-green)]">{Math.round((wins / (wins + losses)) * 100)}%</span> win rate
          <span className="text-[var(--text-muted)]"> - {wins}W / {losses}L in view</span>
        </p>
      )}

      {loading && !data ? (
        <Spinner label="Loading signals..." />
      ) : signals.length === 0 ? (
        <Empty
          icon={<Crosshair size={20} strokeWidth={1.6} />}
          title={filter === "open" ? "No active signals" : "Nothing here yet"}
          sub={filter === "open" ? "No qualifying setup. Capital protected - the agent is watching for real conditions." : "Completed signals appear here with their outcomes."}
        />
      ) : (
        <div className="space-y-3">
          {signals.map((s, i) => (
            <div key={s.id} data-reveal style={{ transitionDelay: `${Math.min(i * 40, 300)}ms` }}>
              <SignalRow signal={s} onClick={() => navigate(`/signals/${s.id}`)} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
