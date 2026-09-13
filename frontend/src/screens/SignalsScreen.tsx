import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Crosshair } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { Signal } from "../lib/types";
import { DemoTag, Empty, Glass, Segmented, Spinner } from "../components/ui";
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
    <div>
      <header className="mb-7 flex items-start justify-between">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight">Signals</h1>
          <p className="mt-1 text-[12.5px] text-txt-low">Every setup explained. You decide.</p>
        </div>
        <DemoTag />
      </header>

      <Segmented
        className="mb-3"
        value={filter}
        onChange={setFilter}
        options={[
          { key: "open", label: "Open" },
          { key: "closed", label: "Closed" },
          { key: "all", label: "All" },
        ]}
      />

      <div className="no-scrollbar -mx-5 mb-5 flex gap-2 overflow-x-auto px-5 lg:mx-0 lg:px-0">
        <Chip active={market === "all"} onClick={() => setMarket("all")}>All markets</Chip>
        {(marketsQ.data?.markets ?? []).map((m) => (
          <Chip key={m.symbol} active={market === m.symbol} onClick={() => setMarket(m.symbol)}>{m.symbol}</Chip>
        ))}
      </div>

      {filter === "closed" && wins + losses > 0 && (
        <p className="mb-4 px-1 text-[12px] text-txt-low">
          <span className="num font-medium text-txt-hi">{Math.round((wins / (wins + losses)) * 100)}%</span> win rate
          <span className="text-txt-faint"> · {wins}W / {losses}L in view</span>
        </p>
      )}

      {loading && !data ? (
        <Spinner label="Loading signals…" />
      ) : signals.length === 0 ? (
        <Empty
          icon={<Crosshair size={22} strokeWidth={1.5} />}
          title={filter === "open" ? "No active signals" : "Nothing here yet"}
          sub={filter === "open" ? "No qualifying setup. Capital protected — the agent is watching for real conditions." : "Completed signals appear here with their outcomes."}
        />
      ) : (
        <Glass pad={false} className="divide-y divide-white/[0.05] overflow-hidden !p-0 animate-fadeUp">
          {signals.map((s) => (
            <SignalRow key={s.id} signal={s} onClick={() => navigate(`/signals/${s.id}`)} />
          ))}
        </Glass>
      )}
    </div>
  );
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`shrink-0 rounded-full border px-3.5 py-1.5 text-[11.5px] font-medium transition-all duration-300 ${
        active ? "border-acc/30 bg-acc/[0.1] text-acc" : "border-white/[0.07] bg-white/[0.02] text-txt-low hover:text-txt-mid"
      }`}
    >
      {children}
    </button>
  );
}
