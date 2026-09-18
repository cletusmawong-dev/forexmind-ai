import { useCallback, useRef, useState, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bell, ChevronRight, Shield, ShieldAlert, Target, TrendingUp, TrendingDown } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { ActivityItem, AgentStatus, MarketCard, Signal } from "../lib/types";
import { AnimatedNumber, DemoTag, Divider, Glass, Logo, ProgressBar, SectionHeader, Spinner, StatusDot, WaveGraphic, HeroArt, ConnectionState } from "../components/ui";
import bullIvory from "../assets/hero-bull.webp";
import bullNavy from "../assets/bull-navy.png";
import bullOnyx from "../assets/bull-onyx.png";
import bullSlate from "../assets/bull-slate.png";

/* each theme has its own bull design (user request 2026-09-17) */
const BULLS: Record<string, string> = {
  ivory: bullIvory,
  navy: bullNavy,
  onyx: bullOnyx,
  slate: bullSlate,
};

/* blend tuned per bull art: dark-bg bulls screen in; navy's light-bg bull multiplies in */
const HERO_BLEND: Record<string, "screen" | "multiply"> = {
  ivory: "screen", navy: "multiply", onyx: "screen", slate: "screen",
};
import { fmtPct, fmtPrice, fmtTime, greeting, shortAgo } from "../lib/format";
import { SignalRow } from "../components/SignalRow";
import { AgentPulse } from "../components/AgentPulse";
import { MorningBriefCard } from "../components/MorningBriefCard";

function useActiveTheme() {
  const [t, setT] = useState(document.documentElement.getAttribute("data-theme") ?? "ivory");
  useEffect(() => {
    const mo = new MutationObserver(() =>
      setT(document.documentElement.getAttribute("data-theme") ?? "ivory"));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);
  return t;
}

export function HomeScreen() {
  const activeTheme = useActiveTheme();
  const heroBull = BULLS[activeTheme] ?? bullIvory;
  const navigate = useNavigate();
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  const status = usePolling<AgentStatus>(() => api.get(endpoints.agentStatus), 4000, [refreshKey]);
  const markets = usePolling<{ markets: MarketCard[] }>(() => api.get(endpoints.markets), 5000, [refreshKey]);
  const activity = usePolling<{ activity: ActivityItem[] }>(() => api.get(endpoints.agentActivity), 4000, [refreshKey]);
  const signals = usePolling<{ signals: Signal[] }>(() => api.get(`${endpoints.signals}?status=open&limit=3`), 6000, [refreshKey]);
  const notifs = usePolling<{ unread: number }>(() => api.get(endpoints.notifications), 12000, [refreshKey]);
  const daily = usePolling<any>(() => api.get(endpoints.daily), 10000, [refreshKey]);
  const exec = usePolling<any>(() => api.get("/api/execution/status"), 20000, [refreshKey]);

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
    if (status.loading) return <Spinner label="Connecting to your agent..." />;
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
        <header className="page-head mb-4 flex items-center justify-between gap-2">
          <span className="lg:hidden"><Logo size={36} /></span>
          <div className="relative flex items-center gap-2.5">
            <div className="sm:block">
              <DemoTag />
            </div>
            <Link
              to="/notifications"
              className="tap relative flex h-10 w-10 items-center justify-center rounded-full border border-[rgba(var(--p-rgb),0.28)] bg-[rgba(240,231,210,0.55)] text-[var(--text-secondary)] backdrop-blur-xl transition-all duration-300 hover:border-[rgba(var(--p-rgb),0.5)] hover:text-[var(--text-primary)]"
              style={{ boxShadow: "inset 0 1px 0 rgba(255,255,255,0.12)" }}
              aria-label="Notifications"
            >
              <Bell size={16} />
              {!!notifs.data?.unread && (
                <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full border-2 border-[var(--bg-primary)] bg-[var(--accent-blue)] px-0.5 text-[8px] font-bold text-[var(--text-primary)]" style={{ boxShadow: "0 0 10px rgba(var(--p-rgb),0.9)" }}>{notifs.data.unread}</span>
              )}
            </Link>
            <div className="flex items-center gap-2">
              <div className="hidden min-[380px]:block text-right leading-tight">
                <div className="text-[9.5px] text-[var(--text-muted)]">Welcome Back</div>
                <div className="flex items-center justify-end gap-1 text-[11.5px] font-bold text-txt-hi">
                  Trader <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent-green)] anim-pulse" style={{ boxShadow: "0 0 8px rgba(var(--p-rgb),0.9)" }} />
                </div>
              </div>
              <div className="flex h-9 w-9 items-center justify-center rounded-full border border-[rgba(var(--p-rgb),0.5)] text-[12px] font-bold text-[var(--c-ink2)]"
                   style={{ background: "linear-gradient(145deg, rgba(59,110,245,0.55), rgba(35,42,59,0.75))", boxShadow: "0 0 18px rgba(var(--p-rgb),0.4), inset 0 1px 0 rgba(255,255,255,0.3)" }}
                   aria-hidden="true">T</div>
            </div>
          </div>
        </header>
        <div className="page-head-divider mb-6" aria-hidden="true" />

        {/* ---- hero ---- */}
        <Glass className="hero-card relative overflow-hidden" pad={false}>
          <img src={heroBull} alt="" aria-hidden="true"
               className="pointer-events-none absolute right-0 top-0 h-full w-[74%] object-cover object-right opacity-80"
               style={{ mixBlendMode: HERO_BLEND[activeTheme] ?? "screen", maskImage: "linear-gradient(to right, transparent 8%, black 52%)", WebkitMaskImage: "linear-gradient(to right, transparent 8%, black 52%)" }} />
          <HeroArt className="right-0 top-0 h-full w-[46%] opacity-75 [mask-image:linear-gradient(to_right,transparent,black_55%)] [-webkit-mask-image:linear-gradient(to_right,transparent,black_55%)]" />
          <div className="relative p-6">
            <p className="text-[13px] font-medium text-[var(--text-secondary)]">{greeting()}</p>
            <p className="mt-0.5 text-[19px] font-bold tracking-tight">
              Traders 
            </p>
            <AnimatedNumber
              value={balance}
              format={(v) => `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
              className="num mt-2.5 block text-[40px] font-extrabold leading-none tracking-[-0.03em] lg:text-[52px]"
            />
            <div className="mt-3.5 flex items-center gap-2">
              <span
                className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold"
                style={{
                  borderColor: up ? "rgba(var(--p-rgb),0.3)" : "rgba(var(--neg-rgb),0.3)",
                  color: up ? "var(--accent-green)" : "var(--accent-red)",
                  background: up ? "rgba(var(--p-rgb),0.08)" : "rgba(var(--neg-rgb),0.08)",
                }}
              >
                {up ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                {fmtPct(p.daily_pl_pct)} today
              </span>
              <span className="flex items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
                <Shield size={11} /> From {p.risk_per_trade_pct}% risk per signal
              </span>
            </div>
          </div>
        </Glass>

          {/* ---- MT5 account + VPS online indicator (vps mode) ---- */}
          {(() => {
            const ex = exec.data;
            if (!ex || ex.mode !== "vps") return null;
            const on = !!ex.enabled;
            const acct = ex.account || {};
            return (
              <div className="glass-2 mt-3 flex items-center justify-between px-4 py-3">
                <div className="flex items-center gap-2.5">
                  <StatusDot tone={ex.bridge?.online ? "green" : "amber"} size={7} pulse={!ex.bridge?.online} />
                  <div>
                    <div className="text-[12px] font-bold tracking-tight">
                      MT5 {ex.bridge?.online ? "- online" : "- offline"}
                      <span className="ml-1.5 font-normal text-[10px] text-txt-faint">{acct.server || ""}</span>
                    </div>
                    <div className="text-[9.5px] text-txt-faint">
                      login {acct.login ?? "-"} - data from MT5
                      {!on && " - auto-trading stopped"}
                    </div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="num text-[13px] font-extrabold">{acct.balance != null ? Number(acct.balance).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "-"}</div>
                  <div className="text-[9px] text-txt-faint">{acct.currency || "USD"} balance</div>
                </div>
              </div>
            );
          })()}

          {(() => {
            const d = daily.data;
            if (!d) return null;
            const hasWalls = (d.daily_profit_target_usd || 0) > 0 || (d.daily_loss_limit_usd || 0) > 0;
            if (!hasWalls) return null;
            const tgt = d.daily_profit_target_usd || 0;
            const lim = d.daily_loss_limit_usd || 0;
            const total = d.total_usd || 0;
            const tgtPct = tgt ? Math.max(0, Math.min(100, (total / tgt) * 100)) : 0;
            const lossPct = lim ? Math.max(0, Math.min(100, (-total / lim) * 100)) : 0;
            const statusWord = d.hit_loss ? "LOSS LIMIT HIT - AUTO ENTRY DISABLED"
              : d.hit_target ? "TARGET HIT - AUTO ENTRY DISABLED" : "ACTIVE";
            return (
              <Glass className="mt-4 px-5 py-4">
                <div className="flex items-center justify-between">
                  <span className="eyebrow !text-[9px]">Daily objective</span>
                  <span className={`rounded-full px-2 py-0.5 text-[9px] font-bold tracking-wide ${d.hit_target || d.hit_loss ? "bg-[var(--accent-amber)] text-[var(--on-desc)]" : "text-pos"}`}>
                    {statusWord}
                  </span>
                </div>
                <div className="mt-2 flex items-baseline gap-2">
                  <span className="text-[19px] font-bold tracking-tight">
                    {(total >= 0 ? "+" : "") + (total || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}$
                  </span>
                  <span className="text-[10px] text-txt-faint">
                    {tgt ? `target ${tgt.toLocaleString()}$` : ""}{tgt && lim ? " - " : ""}{lim ? `limit -${lim.toLocaleString()}$` : ""}
                  </span>
                </div>
                {tgt > 0 && (
                  <div className="mt-2.5">
                    <div className="flex items-center gap-1.5 text-[9.5px] text-txt-faint"><TrendingUp size={11} /> profit target</div>
                    <div className="progress-track mt-1 h-2 w-full overflow-hidden rounded-full"><div className="h-full rounded-full bg-pos" style={{ width: `${tgtPct}%` }} /></div>
                  </div>
                )}
                {lim > 0 && (
                  <div className="mt-2">
                    <div className="flex items-center gap-1.5 text-[9.5px] text-txt-faint"><ShieldAlert size={11} /> loss limit</div>
                    <div className="progress-track mt-1 h-2 w-full overflow-hidden rounded-full"><div className="h-full rounded-full bg-neg" style={{ width: `${lossPct}%` }} /></div>
                  </div>
                )}
                <p className="mt-2 text-[9.5px] leading-relaxed text-txt-faint">{d.realized_basis} - floating: {d.floating_source}</p>
              </Glass>
            );
          })()}

        {/* ---- goal ---- */}
        <Glass level={2} className="mt-4" pad={false}>
          <div className="flex items-center justify-between px-5 pb-4 pt-5">
            <div className="flex items-center gap-2.5">
              <span
                className="flex h-9 w-9 items-center justify-center rounded-[13px] border border-[rgba(var(--p-rgb),0.4)]"
                style={{ background: "linear-gradient(145deg, rgba(var(--p-rgb),0.3), rgba(var(--p-rgb),0.08))", boxShadow: "0 0 18px rgba(var(--p-rgb),0.35), inset 0 1px 0 rgba(255,255,255,0.2)" }}
              >
                <Target size={15} className="text-[var(--c-ink2)]" />
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
          <StatusDot tone="green" size={9} />
          <div className="min-w-0 flex-1">
            <div className="text-[13.5px] font-semibold">
              AI Active <span className="font-normal text-[var(--text-secondary)]">- monitoring {marketList.length || 5} markets</span>
            </div>
            <div className="mt-0.5 truncate text-[11px] text-[var(--text-muted)]">{st.current_task}</div>
          </div>
          <ChevronRight size={16} className="shrink-0 text-[var(--text-muted)]" />
        </Link>

        {/* ---- agent pulse: live proof the agent is working ---- */}
        <div className="mt-4">
          <AgentPulse />
        </div>

        {/* ---- active signals ---- */}
        <SectionHeader className="mt-8" right={<Link to="/signals" className="text-[11.5px] font-semibold text-[#1b69b8] transition hover:text-[var(--accent-cyan)]">View all</Link>}>
          Active Signals
        </SectionHeader>
        <div className="space-y-3">
          {signals.data && signals.data.signals.length > 0 ? (
            signals.data.signals.map((s, i) => (
              <div key={s.id} className="anim-fadeUp" style={{ animationDelay: `${i * 60}ms` }}>
                <SignalRow signal={s} variant="home" onClick={() => navigate(`/signals/${s.id}`)} />
              </div>
            ))
          ) : (
            <Glass level={2} className="px-5 py-6 text-center">
              <p className="text-[13px] font-semibold text-[var(--text-secondary)]">No qualifying setup.</p>
              <p className="mt-1 text-[11.5px] text-[var(--text-muted)]">Capital protected - the agent waits for real conditions.</p>
            </Glass>
          )}
        </div>
      </div>

      {/* ---- right column (desktop) / below (mobile) ---- */}
      <div className="mt-10 lg:col-span-5 lg:mt-0 lg:border-l lg:border-[rgba(var(--warm-rgb),0.07)] lg:pl-10 lg:pt-2">
        <MorningBriefCard />

        <SectionHeader className="mt-8" right={<DemoTag />}>Market Watch</SectionHeader>
        <Glass pad={false} className="overflow-hidden !p-1.5">
          {marketList.length === 0 && <div className="px-4 py-6 text-center text-[12px] text-[var(--text-muted)]">Loading markets...</div>}
          <div className="space-y-1">
            {marketList.map((m) => (
              <div key={m.symbol} className="tap flex items-center gap-3 rounded-2xl px-3.5 py-3 transition hover:bg-[rgba(var(--warm-rgb),0.045)]">
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-bold tracking-tight">{m.symbol}</div>
                  <div className="mt-1 flex items-center gap-1">
                    {m.mtf &&
                      Object.entries(m.mtf).map(([tf, v]) => (
                        <span
                          key={tf}
                          title={`${tf} ${["Bearish", "Neutral", "Bullish"][v + 1]}`}
                          className={`h-1 w-3.5 rounded-full ${
                            v === 1 ? "bg-[var(--accent-green)]/70" : v === -1 ? "bg-[var(--accent-red)]/70" : "bg-[rgba(var(--warm-rgb),0.18)]"
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
                    {m.status === "SIGNAL READY" ? "signal ready" : m.status === "SETUP FORMING" ? "forming" : m.status === "WATCHING" ? "watching" : "no setup"}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Glass>

        <SectionHeader className="mt-8" right={<Link to="/agent" className="text-[11.5px] font-semibold text-[#1b69b8]">Agent</Link>}>
          AI Activity
        </SectionHeader>
        <Glass pad={false} className="!p-5">
          <div className="relative space-y-4">
            <span className="absolute bottom-1 left-[4px] top-1 w-px bg-gradient-to-b from-[rgba(var(--p-rgb),0.35)] to-transparent" />
            {(activity.data?.activity ?? []).slice(0, 6).map((a, i) => (
              <div key={a.id} className="relative flex items-start gap-4 anim-fadeUp" style={{ animationDelay: `${i * 60}ms` }}>
                <span className="relative mt-[5px] flex h-[9px] w-[9px] shrink-0">
                  {i === 0 && <span className="absolute inset-0 rounded-full bg-[var(--accent-blue)] opacity-50 anim-pulse" style={{ transform: "scale(2)" }} />}
                  <span className={`relative h-[9px] w-[9px] rounded-full ${i === 0 ? "bg-[var(--accent-blue)]" : "bg-[rgba(var(--warm-rgb),0.22)]"}`} style={i === 0 ? { boxShadow: "0 0 12px rgba(var(--p-rgb),0.9)" } : {}} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12px] leading-snug text-[var(--text-secondary)]">{a.message}</div>
                  <div className="mt-0.5 text-[9.5px] text-[var(--text-muted)]">
                    {fmtTime(a.ts_override || a.createdAt)} - {shortAgo(a.ts_override || a.createdAt)}
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
