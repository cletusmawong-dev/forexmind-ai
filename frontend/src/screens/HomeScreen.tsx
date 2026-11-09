import { useCallback, useRef, useState, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Bell, CandlestickChart, ChevronRight, Cpu, Eye, Shield, ShieldAlert,
  Target, TrendingUp, TrendingDown, Users, Wallet,
} from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { MarketCard, Signal } from "../lib/types";
import {
  AnimatedNumber, DemoTag, Glass, ProgressBar, SectionHeader, SkeletonList,
  StatusDot, ConnectionState, Pill,
} from "../components/ui";
import { fmtPct, fmtPrice, greeting } from "../lib/format";
import { MarketFlag } from "../components/MarketFlag";
import { MiniChart, useSpark } from "../components/MiniChart";
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

function Skyline({ name, active }: { name: string; active: boolean }) {
  const fill = active ? "#7dffa8" : "rgba(244,247,251,0.8)";
  const key = name.replace(/\s/g, "");
  const bars: Record<string, number[]> = {
    Sydney: [8, 16, 11, 22, 13, 9],
    Tokyo: [7, 26, 14, 20, 11],
    London: [12, 18, 28, 16, 10, 14],
    NewYork: [10, 22, 14, 30, 18, 12],
  };
  const hs = bars[key] || [12, 20, 16, 24, 14];
  return (
    <svg viewBox="0 0 52 52" className="h-full w-full" aria-hidden="true">
      <rect width="52" height="52" fill="#101820" />
      {hs.map((h, i) => (
        <rect key={i} x={3 + i * (46 / hs.length)} y={42 - h} width={Math.max(4, 46 / hs.length - 2)} height={h + 10} rx="1.2" fill={fill} />
      ))}
    </svg>
  );
}

function SessionStory({ name, active, hours }: { name: string; active: boolean; hours: number }) {
  const pct = active ? 1 : Math.max(0.16, 1 - Math.min(hours, 14) / 14);
  const r = 28;
  const circ = 2 * Math.PI * r;
  const label = name === "NewYork" ? "New York" : name;
  return (
    <div className="flex w-[84px] shrink-0 flex-col items-center">
      <div className="relative h-[72px] w-[72px]">
        <svg viewBox="0 0 72 72" className="absolute inset-0">
          <circle cx="36" cy="36" r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="2" />
          <circle
            cx="36" cy="36" r={r} fill="none"
            stroke={active ? "#1fbf75" : "rgba(244,247,251,0.55)"}
            strokeWidth="2.25"
            strokeDasharray={`${circ * pct} ${circ}`}
            strokeLinecap="round"
            transform="rotate(-90 36 36)"
          />
        </svg>
        <div className="absolute inset-[9px] overflow-hidden rounded-full">
          <Skyline name={name} active={active} />
        </div>
      </div>
      <div className="mt-2 text-[13px] font-medium">{label}</div>
      <div className="text-[12px] text-[var(--text-muted)]">{active ? "Open" : `Opens in ${Math.max(1, Math.round(hours))}h`}</div>
    </div>
  );
}

function MarketRow({ row }: { row: { symbol: string; price: number | null; change_pct?: number | null; s1?: { active?: boolean; direction?: string }; s2?: { word?: string; direction?: string } } }) {
  const { change } = useSpark(row.symbol, 96);
  const pct = row.change_pct != null ? row.change_pct : change;
  const up = (pct ?? 0) >= 0;
  const note = row.s2?.word && row.s2.word !== "NO DATA" && row.s2.word !== "NO SETUP"
    ? row.s2.word.toLowerCase()
    : row.s1?.active ? `${row.s1.direction === "SELL" ? "Sell" : "Buy"} live` : null;
  return (
    <Link to={`/charts?symbol=${row.symbol}`}
      className="press flex items-center gap-3 border-b border-white/[0.06] px-4 py-3 active:bg-white/[0.03]">
      <MarketFlag market={row.symbol} size={40} />
      <div className="min-w-0 flex-1">
        <div className="text-[16px] font-semibold tracking-tight">{row.symbol}</div>
        {note && <div className="truncate text-[12px] text-[var(--text-muted)]">{note}</div>}
      </div>
      <div className="w-[72px]">
        <MiniChart market={row.symbol} width={72} height={32} />
      </div>
      <div className="w-[92px] text-right">
        <div className="num text-[15px] font-medium">{row.price == null ? "" : fmtPrice(row.price, row.symbol)}</div>
        {pct != null && (
          <div className={`num text-[13px] font-medium ${up ? "text-pos" : "text-neg"}`}>{fmtPct(pct)}</div>
        )}
      </div>
    </Link>
  );
}

export function HomeScreen() {
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
    if (overview.loading) return <SkeletonList rows={4} />;
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

  const liveSignals = (signals.data?.signals ?? []).filter((s) => !s.extra_signal && !notEnteredSignal(s)).slice(0, 3);
  const todayLabel = acct.today_pl_usd != null
    ? `${acct.today_pl_usd >= 0 ? "+" : "−"}$${Math.abs(acct.today_pl_usd).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
    : acct.daily_pl_pct != null ? fmtPct(acct.daily_pl_pct) : null;

  return (
    <div onTouchStart={onTouchStart} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd} className="lg:grid lg:grid-cols-12 lg:gap-10">
      {/* pull indicator */}
      <div className="pointer-events-none fixed left-1/2 z-50 -translate-x-1/2 transition-all duration-300 lg:hidden" style={{ top: pulling > 0 ? 12 : -40, opacity: pulling > 0 ? 1 : 0 }}>
        <div className="glass-float flex items-center gap-2 rounded-full px-4 py-2 text-[11px] text-[var(--text-secondary)]">
          {pulling > 55 ? "Release to refresh" : "Pull to refresh"}
        </div>
      </div>

      <div className="lg:col-span-7">
        {/* header - mobile version lives in AppHeader (compact, no duplicate brand) */}
        <header className="page-head mb-4 hidden items-center justify-between gap-2 lg:flex">
          <div className="relative flex min-w-0 items-center gap-2">
            <div className="sm:block">
              <DemoTag />
            </div>
            <Link
              to="/notifications"
              className="tap relative flex h-11 w-11 items-center justify-center rounded-full border border-[var(--glass-border)] bg-[var(--bg-elevated)] text-[var(--text-secondary)] transition-all duration-300 hover:border-[rgba(var(--p-rgb),0.5)] hover:text-[var(--text-primary)]"
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
        <div className="page-head-divider mb-3 hidden lg:block" aria-hidden="true" />

        {/* active trading account */}

        <section className="px-1 pb-2 pt-1">
          <p className="text-[15px] text-[var(--text-secondary)]">{greeting()}</p>
          <AnimatedNumber
            value={balance}
            format={(v) => `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
            className="num mt-1 block text-[44px] font-semibold leading-none tracking-[-0.04em]"
          />
          <div className="mt-2 flex items-baseline gap-2">
            <span className={`num text-[15px] font-medium ${todayLabel ? (upToday ? "text-pos" : "text-neg") : "text-[var(--text-muted)]"}`}>
              {todayLabel ?? "No P&L yet"}
            </span>
            <span className="text-[13px] text-[var(--text-muted)]">today</span>
          </div>
          <p className="mt-1 text-[13px] text-[var(--text-muted)]">
            {vpsLive
              ? `Live equity · ${acct.server || "MT5"}`
              : acct.goal_balance != null
                ? "Objective balance"
                : "Connect MT5 to see a live balance"}
          </p>
          <div className="-mx-1 mt-5 h-16">
            <MiniChart market="XAUUSD" width={360} height={64} fill />
          </div>
          <div className="mt-4 grid grid-cols-3 gap-3">
            {[
              ["Open", acct.open_pl != null ? `${Number(acct.open_pl) >= 0 ? "+" : "−"}$${Math.abs(Number(acct.open_pl)).toFixed(0)}` : "—"],
              ["Drawdown", acct.drawdown_pct != null ? `${Number(acct.drawdown_pct).toFixed(2)}%` : "0.00%"],
              ["Risk", acct.risk_per_trade_pct != null ? `${acct.risk_per_trade_pct}%` : "—"],
            ].map(([label, value]) => (
              <div key={label}>
                <div className="text-[12px] text-[var(--text-muted)]">{label}</div>
                <div className="num mt-0.5 text-[15px] font-medium">{value}</div>
              </div>
            ))}
          </div>
          {acct.trading_status && (
            <p className="mt-3 text-[13px] text-[var(--text-secondary)]">{acct.trading_status}</p>
          )}
        </section>

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

        <div className="no-scrollbar -mx-4 mt-7 flex gap-4 overflow-x-auto px-5 pb-1 [scrollbar-width:none]">
          {(ov.sessions ?? []).map((s) => (
            <SessionStory key={s.name} name={s.name} active={s.active} hours={s.opens_in_hours} />
          ))}
        </div>

        <div className="mt-8 flex items-baseline justify-between px-1">
          <h2 className="text-[20px] font-semibold tracking-tight">Markets</h2>
          <Link to="/charts" className="text-[14px] font-medium text-[var(--accent)]">See all</Link>
        </div>
        <div className="-mx-4 mt-2">
          {findingRows.length === 0 && (
            <p className="px-5 py-8 text-center text-[14px] text-[var(--text-muted)]">Markets will show here once the feed answers.</p>
          )}
          {findingRows.map((r) => (
            <MarketRow key={r.symbol} row={r} />
          ))}
        </div>

        {ov.ai && (
          <button onClick={() => navigate("/agent")} className="press mt-8 block w-full px-1 text-left">
            <div className="text-[13px] text-[var(--text-muted)]">{ov.ai.symbol || "Agent"}</div>
            <p className="mt-1 text-[16px] leading-snug tracking-tight">{ov.ai.message}</p>
          </button>
        )}

        <div className="mt-8 flex items-baseline justify-between px-1">
          <h2 className="text-[20px] font-semibold tracking-tight">Signals</h2>
          <Link to="/signals" className="text-[14px] font-medium text-[var(--accent)]">View all</Link>
        </div>
        {liveSignals.length > 0 ? (
          <div className="-mx-4 mt-1">
            {liveSignals.map((s) => (
              <SignalRow key={s.id} signal={s} variant="home" onClick={() => navigate(`/signals/${s.id}`)} />
            ))}
          </div>
        ) : (
          <div className="px-1 py-8">
            <p className="text-[17px] font-medium tracking-tight">Nothing qualifies right now.</p>
            <p className="mt-1 text-[14px] leading-relaxed text-[var(--text-muted)]">The agent stays quiet until a setup is real. Capital stays put.</p>
          </div>
        )}
      </div>

      {/* ---- right column (desktop) / below (mobile) ---- */}
      <div className="mt-10 lg:col-span-5 lg:mt-0 lg:border-l lg:border-[rgba(var(--warm-rgb),0.07)] lg:pl-10 lg:pt-2">
        <MorningBriefCard />

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
