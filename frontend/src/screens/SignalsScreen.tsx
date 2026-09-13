import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Crosshair } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { Signal } from "../lib/types";
import { Chip, DemoTag, Empty, PageHeader, Segmented, Spinner } from "../components/ui";
import { SignalRow } from "../components/SignalRow";

export function SignalsScreen() {
  const [filter, setFilter] = useState("open");
  const [market, setMarket] = useState("all");
  const navigate = useNavigate();

  const qs = useMemo(() => {
    const p = new URLSearchParams({ status: filter, limit: "60" });
    if (market !== "all") p.set("market", market);
    return p.toString();
  }, [filter, market]);

  const { data, loading } = usePolling<{ signals: Signal[]; count: number }>(() => api.get(`${endpoints.signals}?${qs}`), 5000, [filter, market]);
  const marketsQ = usePolling<{ markets: { symbol: string }[] }>(() => api.get(endpoints.markets), 60000);

  const signals = data?.signals ?? [];
  const wins = signals.filter((s) => s.outcome === "WIN").length;
  const losses = signals.filter((s) => s.outcome === "LOSS").length;

  return (
    <div className="anim-fadeUp">
      <PageHeader title="Signals" sub="Every setup explained. You decide." right={<DemoTag />} />

      <Segmented
        className="mb-4"
        value={filter}
        onChange={setFilter}
        options={[
          { key: "open", label: "Open" },
          { key: "closed", label: "Closed" },
          { key: "all", label: "All" },
        ]}
      />

      <div className="no-scrollbar -mx-5 mb-5 flex gap-2 overflow-x-auto px-5 pb-1 lg:mx-0 lg:px-0">
        <Chip active={market === "all"} onClick={() => setMarket("all")}>
          All markets
        </Chip>
        {(marketsQ.data?.markets ?? []).map((m) => (
          <Chip key={m.symbol} active={market === m.symbol} onClick={() => setMarket(m.symbol)}>
            {m.symbol}
          </Chip>
        ))}
      </div>

      {filter === "closed" && wins + losses > 0 && (
        <p className="mb-4 px-1 text-[12px] text-[var(--text-secondary)]">
          <span className="num font-semibold text-[var(--accent-green)]">{Math.round((wins / (wins + losses)) * 100)}%</span> win rate
          <span className="text-[var(--text-muted)]"> · {wins}W / {losses}L in view</span>
        </p>
      )}

      {loading && !data ? (
        <Spinner label="Loading signals…" />
      ) : signals.length === 0 ? (
        <Empty
          icon={<Crosshair size={20} strokeWidth={1.6} />}
          title={filter === "open" ? "No active signals" : "Nothing here yet"}
          sub={filter === "open" ? "No qualifying setup. Capital protected — the agent is watching for real conditions." : "Completed signals appear here with their outcomes."}
        />
      ) : (
        <div className="space-y-3">
          {signals.map((s, i) => (
            <div key={s.id} className="anim-fadeUp" style={{ animationDelay: `${Math.min(i * 40, 300)}ms` }}>
              <SignalRow signal={s} onClick={() => navigate(`/signals/${s.id}`)} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
