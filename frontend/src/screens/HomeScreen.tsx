import { useCallback, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bell, ChevronRight, Shield, Target, TrendingUp, TrendingDown } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { ActivityItem, AgentStatus, MarketCard, Signal } from "../lib/types";
import { AnimatedNumber, DemoTag, Divider, Glass, Logo, ProgressBar, SectionHeader, Spinner, StatusDot, WaveGraphic, ConnectionState } from "../components/ui";
import { fmtPct, fmtPrice, fmtTime, greeting, shortAgo } from "../lib/format";
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
  const up = p.daily_pl_pct >= 0;
  const marketList = markets.data?.markets ?? [];

  return (
    <div onTouchStart={onTouchStart} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd} className="lg:grid lg:grid-cols-12 lg:gap-10">
      {/* pull indicator */}
      <div className="pointer-events-none fixed left-1/2 z-50 -translate-x-1/2 transition-all duration-300 lg:hidden" style={{ top: pulling > 0 ? 12 : -40, opacity: pulling > 0 ? 1 : 0 }}>
        <div className="glass-float flex items-center gap-2 rounded-full px-4 py-2 text-[11px] text-[var(--text-secondary)]">
          {pulling > 55 ? "Release to refresh" : "Pull to refresh"}
        </div>
      </div>

      <div className="lg:col-span-7">
        {/* header */}
        <header className="mb-6 flex items-center justify-between gap-2">
          <Logo size={36} />
          <div className="flex items-center gap-2.5">
            <div className="hidden sm:block">
              <DemoTag />
            </div>
            <Link
              to="/notifications"
              className="tap relative flex h-10 w-10 items-center justify-center rounded-full border border-white/[0.1] bg-white/[0.04] text-[var(--text-secondary)] backdrop-blur-xl"
              aria-label="Notifications"
            >
              <Bell size={16} />
              {!!notifs.data?.unread && (
                <span className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full border-2 border-[var(--bg-primary)] bg-[var(--accent-blue)]" style={{ boxShadow: "0 0 10px rgba(77,124,254,0.9)" }} />
              )}
            </Link>
          </div>
        </header>

        {/* ---- hero ---- */}
        <Glass className="relative overflow-hidden" pad={false}>
          <WaveGraphic className="right-0 top-0 h-full w-[62%] opacity-80" />
          <div className="relative p-6">
            <p className="text-[13px] text-[var(--text-secondary)]">
              {greeting()} <span className="font-semibold text-[var(--text-primary)]">Traders</span> 👋
            </p>
            <AnimatedNumber
              value={balance}
              format={(v) => `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
              className="mt-2 block text-[44px] font-light leading-none lg:text-[54px]"
            />
            <div className="mt-3.5 flex items-center gap-2">
              <span
                className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold"
                style={{
                  borderColor: up ? "rgba(47,217,138,0.3)" : "rgba(251,77,106,0.3)",
                  color: up ? "var(--accent-green)" : "var(--accent-red)",
                  background: up ? "rgba(47,217,138,0.08)" : "rgba(251,77,106,0.08)",
                }}
              >
                {up ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                {fmtPct(p.daily_pl_pct)} today
              </span>
              <span className="hidden items-center gap-1.5 text-[11px] text-[var(--text-muted)] sm:flex">
                <Shield size={11} /> From {p.risk_per_trade_pct}% risk per signal
              </span>
            </div>
          </div>
        </Glass>

        {/* ---- goal ---- */}
        <Glass level={2} className="mt-4" pad={false}>
          <div className="flex items-center justify-between px-5 pb-4 pt-5">
            <div className="flex items-center gap-2.5">
              <span className="flex h-8 w-8 items-center justify-center rounded-full border border-[rgba(77,124,254,0.35)] bg-[rgba(77,124,254,0.12)] glow-blue">
                <Target size={14} className="text-[#8fb4ff]" />
              </span>
              <span className="text-[13.5px] font-semibold">Today's Goal</span>
            </div>
            <span className="num text-[13px] font-semibold text-[var(--text-secondary)]">
              <span className="text-[var(--text-primary)]">{fmtPct(p.daily_pl_pct)}</span> / +{p.objective_pct}%
            </span>
          </div>
          <div className="px-5 pb-5">
            <ProgressBar pct={goalPct} tone={up ? "green" : "blue"} />
          </div>
        </Glass>

        {/* ---- AI active ---- */}
        <Link to="/agent" className="glass glass-hover tap mt-4 flex items-center gap-3.5 p-4">
          <StatusDot tone="green" />
          <div className="min-w-0 flex-1">
            <div className="text-[13.5px] font-semibold">
              AI Active <span className="font-normal text-[var(--text-secondary)]">— monitoring {marketList.length || 5} markets</span>
            </div>
            <div className="mt-0.5 truncate text-[11px] text-[var(--text-muted)]">{st.current_task}</div>
          </div>
          <ChevronRight size={16} className="shrink-0 text-[var(--text-muted)]" />
        </Link>

        {/* ---- active signals ---- */}
        <SectionHeader className="mt-8" right={<Link to="/signals" className="text-[11.5px] font-semibold text-[#8fb4ff] transition hover:text-[var(--accent-cyan)]">View all</Link>}>
          Active Signals
        </SectionHeader>
        <div className="space-y-3">
          {signals.data && signals.data.signals.length > 0 ? (
            signals.data.signals.map((s, i) => (
              <div key={s.id} className="anim-fadeUp" style={{ animationDelay: `${i * 60}ms` }}>
                <SignalRow signal={s} onClick={() => navigate(`/signals/${s.id}`)} />
              </div>
            ))
          ) : (
            <Glass level={2} className="px-5 py-6 text-center">
              <p className="text-[13px] font-semibold text-[var(--text-secondary)]">No qualifying setup.</p>
              <p className="mt-1 text-[11.5px] text-[var(--text-muted)]">Capital protected — the agent waits for real conditions.</p>
            </Glass>
          )}
        </div>
      </div>

      {/* ---- right column (desktop) / below (mobile) ---- */}
      <div className="mt-10 lg:col-span-5 lg:mt-0 lg:border-l lg:border-white/[0.06] lg:pl-10 lg:pt-2">
        <SectionHeader right={<DemoTag />}>Market Watch</SectionHeader>
        <Glass pad={false} className="overflow-hidden !p-1.5">
          {marketList.length === 0 && <div className="px-4 py-6 text-center text-[12px] text-[var(--text-muted)]">Loading markets…</div>}
          <div className="space-y-1">
            {marketList.map((m) => (
              <div key={m.symbol} className="tap flex items-center gap-3 rounded-2xl px-3.5 py-3 transition hover:bg-white/[0.03]">
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-bold tracking-tight">{m.symbol}</div>
                  <div className="mt-1 flex items-center gap-1">
                    {m.mtf &&
                      Object.entries(m.mtf).map(([tf, v]) => (
                        <span
                          key={tf}
                          title={`${tf} ${["Bearish", "Neutral", "Bullish"][v + 1]}`}
                          className={`h-1 w-3.5 rounded-full ${
                            v === 1 ? "bg-[var(--accent-green)]/70" : v === -1 ? "bg-[var(--accent-red)]/70" : "bg-white/15"
                          }`}
                        />
                      ))}
                  </div>
                </div>
                <div className="text-right">
                  <div className="num text-[12.5px] font-semibold">{fmtPrice(m.price, m.symbol)}</div>
                  <div className={`num text-[10px] ${(m.change_pct ?? 0) >= 0 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"}`}>{fmtPct(m.change_pct)}</div>
                </div>
                <div className="w-[72px] text-right">
                  <div className={`text-[10.5px] font-bold ${m.bias === "BUY BIAS" ? "text-[var(--accent-green)]" : m.bias === "SELL BIAS" ? "text-[var(--accent-red)]" : "text-[var(--text-muted)]"}`}>
                    {m.bias.replace(" BIAS", "")}
                  </div>
                  <div className={`text-[8.5px] font-bold uppercase tracking-[0.1em] ${m.status === "SIGNAL READY" ? "text-[var(--accent-cyan)]" : m.status === "SETUP FORMING" ? "text-[var(--accent-amber)]" : "text-[var(--text-muted)]"}`}>
                    {m.status === "SIGNAL READY" ? "◉ signal" : m.status === "SETUP FORMING" ? "forming" : m.status === "WATCHING" ? "watching" : "no setup"}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Glass>

        <SectionHeader className="mt-8" right={<Link to="/agent" className="text-[11.5px] font-semibold text-[#8fb4ff]">Agent</Link>}>
          AI Activity
        </SectionHeader>
        <Glass pad={false} className="!p-5">
          <div className="relative space-y-4">
            <span className="absolute bottom-1 left-[4px] top-1 w-px bg-gradient-to-b from-[rgba(77,124,254,0.35)] to-transparent" />
            {(activity.data?.activity ?? []).slice(0, 6).map((a, i) => (
              <div key={a.id} className="relative flex items-start gap-4 anim-fadeUp" style={{ animationDelay: `${i * 60}ms` }}>
                <span className="relative mt-[5px] flex h-[9px] w-[9px] shrink-0">
                  {i === 0 && <span className="absolute inset-0 rounded-full bg-[var(--accent-blue)] opacity-50 anim-pulse" style={{ transform: "scale(2)" }} />}
                  <span className={`relative h-[9px] w-[9px] rounded-full ${i === 0 ? "bg-[var(--accent-blue)]" : "bg-white/20"}`} style={i === 0 ? { boxShadow: "0 0 12px rgba(77,124,254,0.9)" } : {}} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12px] leading-snug text-[var(--text-secondary)]">{a.message}</div>
                  <div className="mt-0.5 text-[9.5px] text-[var(--text-muted)]">
                    {fmtTime(a.ts_override || a.createdAt)} · {shortAgo(a.ts_override || a.createdAt)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Glass>

        <div className="mt-6 sm:hidden">
          <DemoTag />
        </div>
      </div>
    </div>
  );
}
