import { useCallback, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, Bell } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { ActivityItem, AgentStatus, MarketCard, Signal } from "../lib/types";
import { AnimatedNumber, ConnectionState, DemoTag, Divider, Eyebrow, Glass, GlowDot, Spinner, ThinProgress } from "../components/ui";
import { fmtPct, fmtPrice, fmtTime, greeting, shortAgo, dot as trendDot } from "../lib/format";
import { SignalRow } from "../components/SignalRow";

export function HomeScreen() {
  const navigate = useNavigate();
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  const status = usePolling<AgentStatus>(() => api.get(endpoints.agentStatus), 4000, [refreshKey]);
  const markets = usePolling<{ markets: MarketCard[] }>(() => api.get(endpoints.markets), 5000, [refreshKey]);
  const activity = usePolling<{ activity: ActivityItem[] }>(() => api.get(endpoints.agentActivity), 4000, [refreshKey]);
  const signals = usePolling<{ signals: Signal[] }>(() => api.get(`${endpoints.signals}?status=open&limit=3`), 6000, [refreshKey]);
  const notifs = usePolling<{ unread: number }>(() => api.get(endpoints.notifications), 12000, [refreshKey]);

  // pull to refresh (touch)
  const pullStart = useRef<number | null>(null);
  const [pulling, setPulling] = useState(0);
  const onTouchStart = useCallback((e: React.TouchEvent) => {
    if (window.scrollY <= 0) pullStart.current = e.touches[0].clientY;
  }, []);
  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (pullStart.current !== null) {
      const d = e.touches[0].clientY - pullStart.current;
      if (d > 0) setPulling(Math.min(70, d));
    }
  }, []);
  const onTouchEnd = useCallback(() => {
    if (pulling > 55) refresh();
    pullStart.current = null;
    setPulling(0);
  }, [pulling, refresh]);

  if (!status.data) {
    if (status.loading) return <Spinner label="Connecting to your agent…" />;
    return (
      <div className="pt-10">
        <ConnectionState onRetry={refresh} label="Can't reach your agent" />
      </div>
    );
  }
  const st = status.data;
  const p = st.progress;
  const balance = st.goals.account_balance * (1 + p.daily_pl_pct / 100);
  const goalPct = Math.max(0, (p.daily_pl_pct / Math.max(p.objective_pct, 0.01)) * 100);
  const scanning = st.current_task?.startsWith("Scanning") ? st.current_task.replace("Scanning ", "Scanning ") : st.current_task;

  const marketList = markets.data?.markets ?? [];

  return (
    <div onTouchStart={onTouchStart} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd} className="lg:grid lg:grid-cols-12 lg:gap-10">
      {/* pull indicator */}
      <div
        className="pointer-events-none fixed left-1/2 z-50 -translate-x-1/2 transition-all duration-300 lg:hidden"
        style={{ top: pulling > 0 ? 12 : -40, opacity: pulling > 0 ? 1 : 0 }}
      >
        <div className="glass-float flex items-center gap-2 px-4 py-2 text-[11px] text-txt-mid">
          {pulling > 55 ? "Release to refresh" : "Pull to refresh"}
        </div>
      </div>

      <div className="lg:col-span-7">
        {/* header */}
        <header className="mb-8 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="text-[13px] font-semibold tracking-[0.14em] text-txt-mid">FOREXMIND</span>
            <span className="text-[13px] font-semibold text-acc">AI</span>
          </div>
          <div className="flex items-center gap-2.5">
            <DemoTag />
            <Link to="/notifications" className="tap relative flex h-9 w-9 items-center justify-center rounded-full border border-white/[0.07] bg-white/[0.03] text-txt-mid">
              <Bell size={15} />
              {!!notifs.data?.unread && (
                <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-acc" style={{ boxShadow: "0 0 8px rgba(108,158,255,0.9)" }} />
              )}
            </Link>
          </div>
        </header>

        {/* ---- hero: typography, not boxes ---- */}
        <section className="animate-fadeUp">
          <p className="text-[13px] text-txt-low">{greeting()}</p>
          <div className="mt-2 flex items-end gap-3">
            <AnimatedNumber
              value={balance}
              format={(v) => `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
              className="text-[42px] font-light leading-none text-txt-hi lg:text-[52px]"
            />
          </div>
          <div className={`mt-2.5 flex items-center gap-2 text-[13px] font-medium ${p.daily_pl_pct >= 0 ? "text-pos" : "text-neg"}`}>
            <span className={`h-1 w-1 rounded-full ${p.daily_pl_pct >= 0 ? "bg-pos" : "bg-neg"}`} />
            {fmtPct(p.daily_pl_pct)} today
            <span className="text-[11px] font-normal text-txt-faint">· from {p.risk_per_trade_pct}% risk per signal</span>
          </div>
        </section>

        {/* ---- goal: quiet glass strip ---- */}
        <Glass level={2} className="mt-7 !rounded-[20px] px-5 py-4 animate-fadeUp" pad={false}>
          <div className="flex items-baseline justify-between px-5 pt-4">
            <span className="eyebrow">Today's goal</span>
            <span className="num text-[12px] font-medium text-txt-mid">
              <span className="text-txt-hi">{fmtPct(p.daily_pl_pct)}</span> / +{p.objective_pct}%
            </span>
          </div>
          <div className="px-5 pb-4 pt-3.5">
            <ThinProgress pct={goalPct} tone={p.daily_pl_pct >= 0 ? "pos" : "acc"} />
          </div>
        </Glass>

        {/* ---- AI presence line ---- */}
        <Link to="/agent" className="tap mt-6 flex items-center gap-3 rounded-2xl px-1 py-2">
          <GlowDot tone="pos" />
          <div className="min-w-0 flex-1">
            <span className="text-[12.5px] text-txt-mid">
              AI <span className="font-medium text-txt-hi">Active</span> — monitoring {marketList.length || 5} markets
            </span>
            <div className="truncate text-[11px] text-txt-faint">{scanning}</div>
          </div>
          <ArrowRight size={14} className="shrink-0 text-txt-faint" />
        </Link>

        {/* ---- active signals ---- */}
        <Eyebrow className="mb-1 mt-9" right={<Link to="/signals" className="text-[11px] font-medium text-acc transition hover:text-acc-cyan">View all</Link>}>
          Active signals
        </Eyebrow>
        <div className="mt-2">
          {signals.data && signals.data.signals.length > 0 ? (
            <Glass pad={false} className="divide-y divide-white/[0.05] overflow-hidden !p-0">
              {signals.data.signals.map((s) => (
                <SignalRow key={s.id} signal={s} onClick={() => navigate(`/signals/${s.id}`)} />
              ))}
            </Glass>
          ) : (
            <Glass level={2} pad={false} className="px-5 py-6 text-center">
              <p className="text-[13px] text-txt-mid">No qualifying setup.</p>
              <p className="mt-1 text-[11px] text-txt-faint">Capital protected — the agent waits for real conditions.</p>
            </Glass>
          )}
        </div>
      </div>

      <div className="mt-12 lg:col-span-5 lg:mt-0 lg:border-l lg:border-white/[0.05] lg:pl-10 lg:pt-2">
        {/* ---- market watch ---- */}
        <Eyebrow className="mb-1" right={<DemoTag />}>Market watch</Eyebrow>
        <Glass pad={false} className="mt-2 overflow-hidden !p-0 animate-fadeUp">
          {marketList.length === 0 && <div className="px-5 py-6 text-center text-[12px] text-txt-faint">Loading markets…</div>}
          <div className="divide-y divide-white/[0.05]">
            {marketList.map((m) => {
              const trend = m.mtf ? Object.values(m.mtf).reduce((a, b) => a + b, 0) : 0;
              return (
                <div key={m.symbol} className="flex items-center gap-3 px-5 py-[15px]">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[13.5px] font-semibold tracking-tight">{m.symbol}</span>
                      <span className="text-[9.5px] font-medium text-txt-faint">{m.timeframe}</span>
                    </div>
                    <div className="mt-1 flex items-center gap-1.5">
                      {m.mtf &&
                        Object.entries(m.mtf).map(([tf, v]) => (
                          <span key={tf} title={`${tf} ${["Bearish", "Neutral", "Bullish"][v + 1]}`} className={`h-1 w-3 rounded-full ${trendDot[v] === "text-pos" ? "bg-pos/70" : trendDot[v] === "text-neg" ? "bg-neg/70" : "bg-white/15"}`} />
                        ))}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="num text-[13px] font-medium text-txt-hi">{fmtPrice(m.price, m.symbol)}</div>
                    <div className={`num mt-0.5 text-[10.5px] ${(m.change_pct ?? 0) >= 0 ? "text-pos" : "text-neg"}`}>
                      {fmtPct(m.change_pct)}
                    </div>
                  </div>
                  <div className="w-[74px] text-right">
                    <div className={`text-[11px] font-medium ${
                      m.bias === "BUY BIAS" ? "text-pos" : m.bias === "SELL BIAS" ? "text-neg" : "text-txt-low"
                    }`}>
                      {m.bias.replace(" BIAS", "")}
                    </div>
                    <div className={`mt-0.5 text-[9px] font-semibold uppercase tracking-[0.1em] ${
                      m.status === "SIGNAL READY" ? "text-pos" : m.status === "SETUP FORMING" ? "text-warn/90" : "text-txt-faint"
                    }`}>
                      {m.status === "SIGNAL READY" ? "◉ signal" : m.status === "SETUP FORMING" ? "forming" : m.status === "WATCHING" ? "watching" : "no setup"}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </Glass>

        {/* ---- AI activity timeline ---- */}
        <Eyebrow className="mb-1 mt-9" right={<Link to="/agent" className="text-[11px] font-medium text-acc transition hover:text-acc-cyan">Agent</Link>}>
          AI activity
        </Eyebrow>
        <Glass pad={false} className="mt-2 !p-5">
          <div className="relative space-y-[13px]">
            <span className="absolute bottom-1 left-[3px] top-1 w-px bg-gradient-to-b from-white/[0.09] to-transparent" />
            {(activity.data?.activity ?? []).slice(0, 6).map((a, i) => (
              <div key={a.id} className="relative flex items-start gap-3.5 animate-fadeUp" style={{ animationDelay: `${i * 60}ms` }}>
                <span className="relative mt-[5px] flex h-[7px] w-[7px] shrink-0">
                  {i === 0 && <span className="absolute inset-0 rounded-full bg-acc-cyan opacity-50 animate-pulseSoft" style={{ transform: "scale(2)" }} />}
                  <span className={`relative h-[7px] w-[7px] rounded-full ${i === 0 ? "bg-acc-cyan" : "bg-white/25"}`} style={i === 0 ? { boxShadow: "0 0 10px rgba(124,213,242,0.8)" } : {}} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12px] leading-snug text-txt-mid">{a.message}</div>
                  <div className="mt-0.5 text-[9.5px] text-txt-faint">{fmtTime(a.ts_override || a.createdAt)} · {shortAgo(a.ts_override || a.createdAt)}</div>
                </div>
              </div>
            ))}
          </div>
        </Glass>
      </div>
    </div>
  );
}
