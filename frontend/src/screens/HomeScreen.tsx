import { useCallback, useRef, useState, useEffect, useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Bell, CandlestickChart, ChevronRight, Cpu, Eye, Shield, ShieldAlert,
  Target, TrendingUp, TrendingDown, Users, Wallet,
} from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { MarketCard, Signal } from "../lib/types";
import {
  AnimatedNumber, DemoTag, Glass, Logo, ProgressBar, SectionHeader, Spinner,
  StatusDot, HeroArt, ConnectionState, Pill,
} from "../components/ui";
import { AccountSwitcher } from "../components/AccountSwitcher";
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
import { fmtPct, fmtPrice, greeting } from "../lib/format";
import { SignalRow } from "../components/SignalRow";
import { notEnteredReason as notEnteredSignal } from "../lib/execution";
import { AgentPulse } from "../components/AgentPulse";
import { MorningBriefCard } from "../components/MorningBriefCard";

/* ------------------------------------------------------------------ types */
type Overview = {
  ok?: boolean;
  account?: any;
  sessions?: { name: string; active: boolean; opens_in_hours: number }[];
  findings?: {
    s1: Record<string, { direction?: string; outcome?: string; status?: string; createdAt?: string; active?: boolean }>;
    s2: Record<string, { state?: string; word?: string; direction?: string; zone_type?: string; updatedAt?: string }>;
  };
  ai?: { message: string; at?: string; symbol?: string; action?: string; confidence?: number } | null;
  ts?: string;
};

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

/* S2 machine word -> visual tone (honest words, never manufactured) */
const S2_TONE: Record<string, "pos" | "neg" | "warn" | "acc" | "neutral"> = {
  "SIGNAL READY": "acc",
  "WATCHING": "warn",
  "RETESTING": "acc",
  "DISPLACEMENT": "warn",
  "NO SETUP": "neutral",
};

const S1_WORD = (s1?: { active?: boolean; direction?: string; outcome?: string }) => {
  if (!s1) return "NO SIGNAL YET";
  if (s1.active) return `${s1.direction === "SELL" ? "SELL" : "BUY"} LIVE`;
  if (s1.outcome) return `LAST ${String(s1.outcome).replace(/_/g, " ")}`;
  return "NO SIGNAL YET";
};

const S1_TONE = (s1?: { active?: boolean; direction?: string; outcome?: string }) =>
  s1?.active ? (s1.direction === "SELL" ? "neg" : "pos") : "neutral";

export function HomeScreen() {
  const activeTheme = useActiveTheme();
  const heroBull = BULLS[activeTheme] ?? bullIvory;
  const navigate = useNavigate();
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  /* ONE aggregate call (cached server-side) + three light polls. The old Home
     fired 7 pollers every few seconds; these four carry the same answers. */
  const overview = usePolling<Overview>(() => api.get("/api/home/overview"), 8000, [refreshKey]);
  const markets = usePolling<{ markets: MarketCard[] }>(() => api.get(endpoints.markets), 8000, [refreshKey]);
  const signals = usePolling<{ signals: Signal[] }>(() => api.get(`${endpoints.signals}?status=open&limit=6`), 8000, [refreshKey]);
  const notifs = usePolling<{ unread: number }>(() => api.get(endpoints.notifications), 20000, [refreshKey]);

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

  if (!overview.data) {
    if (overview.loading) return <Spinner label="Connecting to your agent..." />;
    return (
      <div className="pt-10">
        <ConnectionState onRetry={refresh} label="Can't reach your agent" />
      </div>
    );
  }
  const ov = overview.data;
  const acct = ov.account || {};
  const vpsLive = acct.execution_mode === "vps" && acct.bridge_online && acct.equity != null;
  const balance = vpsLive
    ? Number(acct.equity)                                            // broker truth
    : Number(acct.goal_balance ?? 0) * (1 + Number(acct.daily_pl_pct ?? 0) / 100);
  const upToday = Number(acct.today_pl_usd ?? acct.daily_pl_pct ?? 0) >= 0;
  const marketList = markets.data?.markets ?? [];
  const priceOf = useMemo(() => {
    const m = new Map<string, MarketCard>();
    marketList.forEach((x) => m.set(x.symbol.toUpperCase(), x));
    return (sym: string) => m.get(sym.toUpperCase());
  }, [marketList]);

  /* per-symbol findings rows: markets the agent tracks, joined with the
     persisted machine states (never manufactured - missing = NO DATA) */
  const s1map = ov.findings?.s1 ?? {};
  const s2map = ov.findings?.s2 ?? {};
  const findingRows = marketList.slice(0, 8).map((m) => ({
    symbol: m.symbol.toUpperCase(),
    price: m.price,
    change_pct: m.change_pct,
    s1: s1map[m.symbol.toUpperCase()],
    s2: s2map[m.symbol.toUpperCase()],
    s2Word: s2map[m.symbol.toUpperCase()]?.word ?? "NO DATA",
  }));

  const statusTone =
    String(acct.trading_status || "").startsWith("TARGET HIT") ? "warn" :
    String(acct.trading_status || "").startsWith("LOSS LIMIT") ? "neg" :
    acct.execution_mode === "off" ? "neutral" : "pos";

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
          <div className="relative flex min-w-0 items-center gap-2">
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
        <div className="page-head-divider mb-3" aria-hidden="true" />

        {/* active trading account */}
        <div className="mb-4 flex justify-end"><AccountSwitcher /></div>

        {/* ---- hero: ANSWER 1 - your account -------------------------------- */}
        <Glass className="hero-card relative overflow-hidden" pad={false} data-reveal>
          <img src={heroBull} alt="" aria-hidden="true"
               className="pointer-events-none absolute right-0 top-0 h-full w-[74%] object-cover object-right opacity-80"
               style={{ mixBlendMode: HERO_BLEND[activeTheme] ?? "screen", maskImage: "linear-gradient(to right, transparent 8%, black 52%)", WebkitMaskImage: "linear-gradient(to right, transparent 8%, black 52%)" }} />
          <HeroArt className="right-0 top-0 h-full w-[46%] opacity-75 [mask-image:linear-gradient(to_right,transparent,black_55%)] [-webkit-mask-image:linear-gradient(to_right,transparent,black_55%)]" />
          <div className="relative p-6">
            <div className="flex items-center justify-between gap-2">
              <p className="text-[13px] font-medium text-[var(--text-secondary)]">{greeting()}</p>
              <Pill tone={statusTone as any}>{acct.trading_status || "STATUS UNAVAILABLE"}</Pill>
            </div>
            <AnimatedNumber
              value={balance}
              format={(v) => `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
              className="num mt-2.5 block text-[38px] font-extrabold leading-none tracking-[-0.03em] lg:text-[48px]"
            />
            <p className="mt-1.5 text-[10px] text-txt-faint">
              {vpsLive
                ? `MT5 live equity - ${acct.server || "your VPS terminal"}`
                : acct.goal_balance != null
                  ? "Objective account balance"
                  : "DATA UNAVAILABLE - connect your MT5 bridge"}
            </p>

            {/* account facts: today / open / drawdown - broker truth or honest empty */}
            <div className="mt-4 grid grid-cols-3 gap-2">
              <div className="rounded-2xl border border-[rgba(var(--warm-rgb),0.09)] bg-[rgba(var(--bg-primary-rgb,20,24,33),0.35)] px-3 py-2.5">
                <div className="text-[8.5px] font-bold uppercase tracking-[0.14em] text-txt-faint">Today P/L</div>
                <div className={`num mt-1 text-[14px] font-extrabold ${upToday ? "text-pos" : "text-neg"}`}>
                  {acct.today_pl_usd != null
                    ? `${acct.today_pl_usd >= 0 ? "+" : ""}$${Math.abs(acct.today_pl_usd).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                    : acct.daily_pl_pct != null ? fmtPct(acct.daily_pl_pct) : "-"}
                </div>
              </div>
              <div className="rounded-2xl border border-[rgba(var(--warm-rgb),0.09)] bg-[rgba(var(--bg-primary-rgb,20,24,33),0.35)] px-3 py-2.5">
                <div className="text-[8.5px] font-bold uppercase tracking-[0.14em] text-txt-faint">Open P/L</div>
                <div className={`num mt-1 text-[14px] font-extrabold ${Number(acct.open_pl ?? 0) >= 0 ? "text-pos" : "text-neg"}`}>
                  {acct.open_pl != null
                    ? `${acct.open_pl >= 0 ? "+" : "-"}$${Math.abs(acct.open_pl).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                    : "-"}
                </div>
              </div>
              <div className="rounded-2xl border border-[rgba(var(--warm-rgb),0.09)] bg-[rgba(var(--bg-primary-rgb,20,24,33),0.35)] px-3 py-2.5">
                <div className="text-[8.5px] font-bold uppercase tracking-[0.14em] text-txt-faint">Drawdown</div>
                <div className="num mt-1 text-[14px] font-extrabold text-neg">
                  {acct.drawdown_pct != null && acct.drawdown_pct > 0 ? `${acct.drawdown_pct.toFixed(2)}%` : "0.00%"}
                </div>
              </div>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              {acct.daily_pl_pct != null && acct.objective_pct != null && (
                <span className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold"
                      style={{
                        borderColor: upToday ? "rgba(var(--p-rgb),0.3)" : "rgba(var(--neg-rgb),0.3)",
                        color: upToday ? "var(--accent-green)" : "var(--accent-red)",
                        background: upToday ? "rgba(var(--p-rgb),0.08)" : "rgba(var(--neg-rgb),0.08)",
                      }}>
                  {upToday ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                  {fmtPct(acct.daily_pl_pct)} / +{acct.objective_pct}% objective
                </span>
              )}
              <span className="flex items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
                <Shield size={11} /> {acct.risk_per_trade_pct ?? "-"}% risk per signal
                {acct.execution_mode === "vps" && (acct.trades_today != null) ? ` - ${acct.trades_today}/${acct.max_per_day ?? "-"} trades today` : ""}
              </span>
            </div>
          </div>
        </Glass>

        {/* MT5 bridge line (vps mode) */}
        {acct.execution_mode === "vps" && (
          <div className="glass-2 mt-3 flex items-center justify-between px-4 py-3">
            <div className="flex items-center gap-2.5">
              <StatusDot tone={acct.bridge_online ? "green" : "amber"} size={7} pulse={!acct.bridge_online} />
              <div>
                <div className="text-[12px] font-bold tracking-tight">
                  MT5 {acct.bridge_online ? "- online" : "- offline"}
                  <span className="ml-1.5 font-normal text-[10px] text-txt-faint">{acct.server || ""}</span>
                </div>
                <div className="text-[9.5px] text-txt-faint">
                  {acct.open_positions != null ? `${acct.open_positions} open position${acct.open_positions === 1 ? "" : "s"} - ` : ""}
                  data from MT5{acct.execution_enabled === false ? " - auto-trading stopped" : ""}
                </div>
              </div>
            </div>
            <div className="text-right">
              <div className="num text-[13px] font-extrabold">{acct.balance != null ? Number(acct.balance).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "-"}</div>
              <div className="text-[9px] text-txt-faint">{acct.currency || "USD"} balance</div>
            </div>
          </div>
        )}

        {/* daily walls */}
        {(Number(acct.daily_target_usd) > 0 || Number(acct.daily_limit_usd) > 0) && (
          <Glass className="mt-3 px-5 py-4">
            <div className="flex items-center justify-between">
              <span className="eyebrow !text-[9px]">Daily objective</span>
              <span className={`rounded-full px-2 py-0.5 text-[9px] font-bold tracking-wide ${acct.hit_target || acct.hit_loss ? "bg-[var(--accent-amber)] text-[var(--on-desc)]" : "text-pos"}`}>
                {acct.hit_loss ? "LOSS LIMIT HIT - AUTO ENTRY DISABLED"
                  : acct.hit_target ? "TARGET HIT - AUTO ENTRY DISABLED" : "ACTIVE"}
              </span>
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-[19px] font-bold tracking-tight">
                {(Number(acct.today_pl_usd ?? 0) >= 0 ? "+" : "") + Number(acct.today_pl_usd ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}$
              </span>
              <span className="text-[10px] text-txt-faint">
                {Number(acct.daily_target_usd) > 0 ? `target ${Number(acct.daily_target_usd).toLocaleString()}$` : ""}
                {Number(acct.daily_target_usd) > 0 && Number(acct.daily_limit_usd) > 0 ? " - " : ""}
                {Number(acct.daily_limit_usd) > 0 ? `limit -${Number(acct.daily_limit_usd).toLocaleString()}$` : ""}
              </span>
            </div>
            {Number(acct.daily_target_usd) > 0 && (
              <div className="mt-2.5">
                <div className="flex items-center gap-1.5 text-[9.5px] text-txt-faint"><TrendingUp size={11} /> profit target</div>
                <div className="progress-track mt-1 h-2 w-full overflow-hidden rounded-full">
                  <div className="h-full rounded-full bg-pos" style={{ width: `${Math.max(0, Math.min(100, (Number(acct.today_pl_usd ?? 0) / Number(acct.daily_target_usd)) * 100))}%` }} />
                </div>
              </div>
            )}
            {Number(acct.daily_limit_usd) > 0 && (
              <div className="mt-2">
                <div className="flex items-center gap-1.5 text-[9.5px] text-txt-faint"><ShieldAlert size={11} /> loss limit</div>
                <div className="progress-track mt-1 h-2 w-full overflow-hidden rounded-full">
                  <div className="h-full rounded-full bg-neg" style={{ width: `${Math.max(0, Math.min(100, (-Number(acct.today_pl_usd ?? 0) / Number(acct.daily_limit_usd)) * 100))}%` }} />
                </div>
              </div>
            )}
            <p className="mt-2 text-[9.5px] leading-relaxed text-txt-faint">{acct.realized_basis} - floating: {acct.floating_source}</p>
          </Glass>
        )}

        {/* ---- ANSWER 2 - market sessions ---------------------------------- */}
        <div className="mt-4 grid grid-cols-4 gap-2">
          {(ov.sessions ?? []).map((s) => (
            <div key={s.name}
                 className={`glass flex flex-col items-center gap-0.5 px-1 py-2.5 text-center transition-all ${s.active ? "!border-[rgba(var(--p-rgb),0.4)]" : "opacity-60"}`}
                 style={s.active ? { boxShadow: "0 0 16px rgba(var(--p-rgb),0.25)" } : undefined}>
              <div className="flex items-center gap-1 text-[10.5px] font-bold tracking-tight">
                {s.active && <StatusDot tone="green" size={5} pulse />}
                {s.name}
              </div>
              <div className="text-[8.5px] text-txt-faint">{s.active ? "OPEN" : `opens in ${s.opens_in_hours}h`}</div>
            </div>
          ))}
        </div>

        {/* ---- ANSWER 3 - what FOREXMIND finds ------------------------------ */}
        <SectionHeader className="mt-6" right={<Link to="/charts" className="text-[11.5px] font-semibold text-[#1b69b8] transition hover:text-[var(--accent-cyan)]">Open chart</Link>}>
          What FOREXMIND finds
        </SectionHeader>
        <Glass pad={false} className="overflow-hidden !p-1.5" data-reveal>
          {findingRows.length === 0 && (
            <div className="px-4 py-6 text-center text-[12px] text-[var(--text-muted)]">DATA UNAVAILABLE - market list not loaded yet.</div>
          )}
          <div className="space-y-1">
            {findingRows.map((r) => (
              <Link key={r.symbol} to={`/charts?symbol=${r.symbol}`}
                    className="tap flex items-center gap-3 rounded-2xl px-3.5 py-3 transition hover:bg-[rgba(var(--warm-rgb),0.045)]">
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2">
                    <span className="text-[13px] font-bold tracking-tight">{r.symbol}</span>
                    <span className="num text-[11px] text-[var(--text-muted)]">{fmtPrice(r.price, r.symbol)}</span>
                    <span className={`num text-[9.5px] ${(r.change_pct ?? 0) >= 0 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"}`}>{fmtPct(r.change_pct)}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <span className="text-[8.5px] font-bold uppercase tracking-[0.08em] text-txt-faint">9/21 EMA</span>
                    <Pill tone={S1_TONE(r.s1) as any} className="!px-1.5 !py-0.5 !text-[8.5px]">{S1_WORD(r.s1)}</Pill>
                  </div>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <span className="text-[8.5px] font-bold uppercase tracking-[0.08em] text-txt-faint">S/D + FVG - 15M</span>
                  <Pill tone={(S2_TONE[r.s2Word] ?? "neutral") as any} className="!px-2 !py-0.5 !text-[9px]">
                    {r.s2Word}{r.s2?.direction ? ` - ${r.s2.direction}` : ""}
                  </Pill>
                </div>
              </Link>
            ))}
          </div>
        </Glass>

        {/* ---- ANSWER 4 - compact AI insight (real notes only) --------------- */}
        <SectionHeader className="mt-6" right={<Link to="/agent" className="text-[11.5px] font-semibold text-[#1b69b8]">Ask the AI</Link>}>
          AI insight
        </SectionHeader>
        {ov.ai ? (
          <Glass level={2} className="cursor-pointer tap !p-4" data-reveal onClick={() => navigate("/agent")}>
            <div className="flex items-center gap-2">
              <Cpu size={13} className="text-[var(--accent-cyan)]" />
              <span className="text-[11px] font-bold uppercase tracking-[0.1em] text-[var(--accent-cyan)]">
                {ov.ai.symbol ? `${ov.ai.symbol} - ` : ""}{ov.ai.action || "AI MANAGER"}
              </span>
              {ov.ai.confidence != null && <span className="ml-auto text-[9.5px] text-txt-faint">confidence {ov.ai.confidence}%</span>}
            </div>
            <p className="mt-2 line-clamp-3 text-[12px] leading-relaxed text-[var(--text-secondary)]">{ov.ai.message}</p>
          </Glass>
        ) : (
          <Glass level={2} className="!p-4" data-reveal>
            <p className="text-[12px] text-[var(--text-muted)]">No AI note yet - the AI manager speaks when it has a real trade decision to explain.</p>
          </Glass>
        )}

        {/* ---- ANSWER 5 - quick access --------------------------------------- */}
        <div className="mt-4 grid grid-cols-5 gap-2">
          {([["/charts", "Chart", CandlestickChart], ["/signals", "Signals", Eye],
             ["/positions", "Positions", Wallet], ["/agent", "AI", Cpu],
             ["/community", "Community", Users]] as const).map(
            ([to, label, Icon]) => (
              <Link key={to} to={to}
                className="glass tap flex flex-col items-center gap-1.5 px-1 py-3 text-[9.5px] font-medium text-txt-mid transition-all hover:!border-[rgba(var(--p-rgb),0.4)]">
                <Icon size={16} className="text-[var(--text-secondary)]" />
                {label}
              </Link>
            ))}
        </div>

        {/* ---- agent pulse: live proof the agent is working ------------------- */}
        <div className="mt-4">
          <AgentPulse />
        </div>

        {/* ---- active signals -------------------------------------------------- */}
        <SectionHeader className="mt-8" right={<Link to="/signals" className="text-[11.5px] font-semibold text-[#1b69b8] transition hover:text-[var(--accent-cyan)]">View all</Link>}>
          Active Signals
        </SectionHeader>
        <div className="space-y-3">
          {signals.data && signals.data.signals.filter((s) => !s.extra_signal && !notEnteredSignal(s)).slice(0, 3).length > 0 ? (
            signals.data.signals.filter((s) => !s.extra_signal && !notEnteredSignal(s)).slice(0, 3).map((s, i) => (
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
        <Glass pad={false} className="overflow-hidden !p-1.5" data-reveal>
          {marketList.length === 0 && <div className="px-4 py-6 text-center text-[12px] text-[var(--text-muted)]">DATA UNAVAILABLE - markets not loaded yet.</div>}
          <div className="space-y-1">
            {marketList.map((m) => {
              const f2 = s2map[m.symbol.toUpperCase()];
              return (
                <Link key={m.symbol} to={`/charts?symbol=${m.symbol.toUpperCase()}`}
                      className="tap flex items-center gap-3 rounded-2xl px-3.5 py-3 transition hover:bg-[rgba(var(--warm-rgb),0.045)]">
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
                  <div className="w-[86px] text-right">
                    <div className={`text-[10.5px] font-bold ${m.bias === "BUY BIAS" ? "text-[var(--accent-green)]" : m.bias === "SELL BIAS" ? "text-[var(--accent-red)]" : "text-[var(--text-muted)]"}`}>
                      {m.bias.replace(" BIAS", "")}
                    </div>
                    <div className={`text-[8.5px] font-bold uppercase tracking-[0.1em] ${
                      f2?.word === "SIGNAL READY" ? "text-[var(--accent-cyan)]"
                        : f2?.word === "WATCHING" || f2?.word === "RETESTING" || f2?.word === "DISPLACEMENT" ? "text-[var(--accent-amber)]"
                        : "text-[var(--text-muted)]"}`}>
                      {f2?.word ? f2.word.toLowerCase() : "no data"}
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
        </Glass>

        {/* today's goal (objective bar) */}
        {acct.daily_pl_pct != null && acct.objective_pct != null && (
          <Glass level={2} className="mt-6" pad={false} data-reveal>
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
                <span className="text-[var(--text-primary)]">{fmtPct(acct.daily_pl_pct)}</span> / +{acct.objective_pct}%
              </span>
            </div>
            <div className="px-5 pb-5">
              <ProgressBar
                pct={Math.max(0, (Number(acct.daily_pl_pct) / Math.max(Number(acct.objective_pct), 0.01)) * 100)}
                tone={Number(acct.daily_pl_pct) >= 0 ? "green" : "blue"}
              />
            </div>
          </Glass>
        )}

        <div className="mt-6 sm:hidden">
          <DemoTag />
        </div>
      </div>
    </div>
  );
}
