import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {Sparkles, Send, Brain} from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import { DemoTag, Divider, Eyebrow, Glass, GlowDot, Pill, Segmented, Spinner } from "../components/ui";
import { Logo } from "../components/Logo";

export function SettingsScreen() {
  const goals = usePolling<any>(() => api.get(endpoints.goals), 10000);
  const risk = usePolling<any>(() => api.get(endpoints.settings), 10000);
  const info = usePolling<any>(() => api.get(endpoints.systemInfo), 30000);
  const me = usePolling<any>(() => api.get(endpoints.me), 30000);
  const [toast, setToast] = useState("");
  const [theme, setThemeState] = useState<"ivory" | "navy" | "onyx" | "slate">(() => {
    const t = localStorage.getItem("fm_theme");
    return t === "navy" || t === "onyx" || t === "slate" ? t : "ivory";
  });

  const THEME_BAR: Record<string, string> = {
    ivory: "#f7f1e3", navy: "#cdd4e0", onyx: "#0a0a0c", slate: "#2e3c55",
  };

  const setTheme = (t: "ivory" | "navy" | "onyx" | "slate") => {
    setThemeState(t);
    localStorage.setItem("fm_theme", t);
    if (t === "ivory") {
      document.documentElement.removeAttribute("data-theme");
      document.querySelector('meta[name="theme-color"]')?.setAttribute("content", "#f7f1e3");
    } else {
      document.documentElement.setAttribute("data-theme", t);
      document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_BAR[t]);
    }
  };

  const [balance, setBalance] = useState("");
  const [daily, setDaily] = useState("");
  const [weekly, setWeekly] = useState("");
  const [riskPct, setRiskPct] = useState("");
  const [maxLoss, setMaxLoss] = useState("");
  const [maxSignals, setMaxSignals] = useState("");
  const [minRR, setMinRR] = useState("");

  useEffect(() => {
    if (goals.data) {
      setBalance(String(goals.data.account_balance));
      setDaily(String(goals.data.daily_objective_pct));
      setWeekly(String(goals.data.weekly_objective_pct));
    }
  }, [goals.data]);
  const [timeframes, setTimeframes] = useState<string[]>(["15M"]);
  const [markets, setMarkets] = useState<string[]>([]);
  const [profitTarget, setProfitTarget] = useState("");
  const [lossLimitUsd, setLossLimitUsd] = useState("");
  const [onLossLimit, setOnLossLimit] = useState<"stop_entries" | "stop_and_close">("stop_entries");
  const [sessions, setSessions] = useState<string[]>([]);
  const [marketSessions, setMarketSessions] = useState<Record<string, string[]>>({});
  const [sessionTz, setSessionTz] = useState("UTC");
  const [acctType, setAcctType] = useState<"personal" | "propfirm">("personal");
  const [propDaily, setPropDaily] = useState("5");
  const [propTotal, setPropTotal] = useState("10");
  const [propTarget, setPropTarget] = useState("8");
  const [propBuffer, setPropBuffer] = useState("20");
  const [propStart, setPropStart] = useState("");
  useEffect(() => {
    if (risk.data) {
      setRiskPct(String(risk.data.risk_per_trade_pct));
      setMaxLoss(String(risk.data.max_daily_loss_pct));
      setMaxSignals(String(risk.data.max_signals_per_day));
      setMinRR(String(risk.data.min_rr));
      setTimeframes(risk.data.signal_timeframes ?? ["15M"]);
      setMarkets(risk.data.allowed_markets ?? []);
      setSessions(risk.data.sessions ?? ["London", "NewYork", "Asian", "Late"]);
      setProfitTarget(risk.data.daily_profit_target_usd ? String(risk.data.daily_profit_target_usd) : "");
      setLossLimitUsd(risk.data.daily_loss_limit_usd ? String(risk.data.daily_loss_limit_usd) : "");
      setOnLossLimit(risk.data.on_loss_limit === "stop_and_close" ? "stop_and_close" : "stop_entries");
      setMarketSessions(risk.data.market_sessions ?? {});
      setSessionTz(risk.data.session_tz || "UTC");
      const at = risk.data.account_type === "propfirm" ? "propfirm" : "personal";
      setAcctType(at);
      const pr = risk.data.prop_rules ?? {};
      if (pr.daily_drawdown_pct != null) setPropDaily(String(pr.daily_drawdown_pct));
      if (pr.max_total_drawdown_pct != null) setPropTotal(String(pr.max_total_drawdown_pct));
      if (pr.profit_target_pct != null) setPropTarget(String(pr.profit_target_pct));
      if (pr.daily_dd_buffer_pct != null) setPropBuffer(String(pr.daily_dd_buffer_pct));
      if (pr.account_start_balance) setPropStart(String(pr.account_start_balance));
    }
  }, [risk.data]);

  const TF_OPTIONS = ["15M", "1H", "4H", "1D"];
  const SESSION_OPTIONS = ["Asian", "London", "NewYork", "Late"];
  const toggleTf = (tf: string) => {
    const next = timeframes.includes(tf)
      ? timeframes.filter((t) => t !== tf)
      : [...timeframes, tf];
    if (next.length === 0) {
      flash("Keep at least one timeframe.");
      return;
    }
    setTimeframes(next);
  };

  const toggleMk = (mk: string) => {
    setMarkets((prev) =>
      prev.includes(mk) ? prev.filter((m) => m !== mk) : [...prev, mk]);
  };

  const toggleSession = (sn: string) => {
    setSessions((prev) =>
      prev.includes(sn) ? prev.filter((x) => x !== sn) : [...prev, sn]);
  };
  const toggleMarketSession = (mk: string, sn: string) => {
    setMarketSessions((prev) => {
      const cur = prev[mk] ?? [];
      const next = cur.includes(sn) ? cur.filter((x) => x !== sn) : [...cur, sn];
      const out = { ...prev };
      if (next.length === 0) delete out[mk];   // untick all -> follow global again
      else out[mk] = next;
      return out;
    });
  };

  const flash = (m: string) => {
    setToast(m);
    setTimeout(() => setToast(""), 3000);
  };

  const saveGoals = async () => {
    await api.patch(endpoints.goals, {
      account_balance: parseFloat(balance),
      daily_objective_pct: parseFloat(daily),
      weekly_objective_pct: parseFloat(weekly),
    });
    flash("Objective updated.");
  };
  const saveRisk = async () => {
    try {
      const propRules: Record<string, number> | null = acctType === "propfirm"
        ? {
            daily_drawdown_pct: parseFloat(propDaily) || 5,
            max_total_drawdown_pct: parseFloat(propTotal) || 10,
            profit_target_pct: parseFloat(propTarget) || 8,
            daily_dd_buffer_pct: propBuffer === "" ? 20 : parseFloat(propBuffer),
            ...(propStart !== "" ? { account_start_balance: parseFloat(propStart) || 0 } : {}),
          }
        : null;
      await api.patch(endpoints.settings, {
        risk_per_trade_pct: parseFloat(riskPct),
        max_daily_loss_pct: parseFloat(maxLoss),
        max_signals_per_day: parseInt(maxSignals),
        min_rr: parseFloat(minRR),
        signal_timeframes: timeframes,
        allowed_markets: markets,
        sessions,
        market_sessions: marketSessions,
        session_tz: sessionTz,
        daily_profit_target_usd: parseFloat(profitTarget) || 0,
        daily_loss_limit_usd: parseFloat(lossLimitUsd) || 0,
        on_loss_limit: onLossLimit,
        account_type: acctType,
        prop_rules: propRules,
      });
      flash(acctType === "propfirm"
        ? `Saved. PROP MODE - the engine stops new signals at ${100 - (parseFloat(propBuffer) || 20)}% of your ${propDaily || 5}% daily drawdown.`
        : `Saved. Personal account - daily limit ${maxLoss}%.`);
    } catch (e: any) {
      flash(e.message);
      return;
    }
  };

  if (!goals.data || !risk.data) return <Spinner label="Loading settings..." />;

  return (
    <div className="animate-fadeUp">
      <header className="mb-7 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="icon-chip icon-chip-violet" aria-hidden="true"><Brain size={20} /></span>
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight">ForexMind</h1>
            <p className="text-[10.5px] text-txt-faint">Agent objective - never a command to trade.</p>
          </div>
        </div>
        <DemoTag />
      </header>

      {toast && <div className="glass-2 mb-5 px-4 py-3 text-[12px] font-medium text-acc-cyan">{toast}</div>}

      {/* ---- objective ---- */}
      {/* ---- appearance: theme switcher ---- */}
      <Eyebrow>Appearance</Eyebrow>
      <Glass className="mt-2">
        <div className="eyebrow !text-[9px] mb-2.5">Design - applies instantly, saved on this device</div>
        <div className="grid grid-cols-2 gap-2">
          {([["ivory", "Ivory", "Cream + teal", "#f7f1e3", "#1aa98c"],
             ["navy", "Navy & Gold", "Silver + navy", "#cdd4e0", "#1d4f8f"],
             ["onyx", "Onyx & Gold", "Black + gold", "#141419", "#d4af37"],
             ["slate", "Slate & Gold", "Blue-gray + navy", "#5f6d83", "#1d4f8f"]] as const).map(([k, label, desc, dotA, dotB]) => (
            <button
              key={k}
              onClick={() => setTheme(k as any)}
              aria-pressed={theme === k}
              className={`tap min-h-[76px] rounded-2xl border p-3 text-left ${
                theme === k ? "chip-on" : "chip-off"
              }`}
            >
              <span className="flex items-center gap-1.5">
                <span className="h-3.5 w-3.5 rounded-full border border-black/10" style={{ background: dotA }} />
                <span className="h-3.5 w-3.5 rounded-full border border-black/10" style={{ background: dotB }} />
                <span className="h-3.5 w-3.5 rounded-full border border-black/10" style={{ background: "#d4af37" }} />
              </span>
              <span className="mt-2 block text-[12.5px] font-bold">{label}</span>
              <span className={`mt-0.5 block text-[10px] leading-snug ${theme === k ? "text-[var(--on-desc)]" : "text-txt-faint"}`}>{desc}</span>
            </button>
          ))}
        </div>
      </Glass>

      <Eyebrow className="mt-9">Agent objective</Eyebrow>
      <p className="mb-3 mt-1 px-1 text-[11px] text-txt-faint">Guides the research focus - never a command to trade.</p>
      <Glass>
        <div className="grid grid-cols-3 gap-4">
          <Field label="Balance $" value={balance} onChange={setBalance} />
          <Field label="Daily %" value={daily} onChange={setDaily} />
          <Field label="Weekly %" value={weekly} onChange={setWeekly} />
        </div>
        <button className="btn-primary mt-5 w-full" onClick={saveGoals}>
          <Sparkles size={14} /> Save objective
        </button>
      </Glass>

      {/* ---- risk ---- */}
      <Eyebrow className="mt-9">Risk management</Eyebrow>
      <Glass className="mt-2">
        <div className="eyebrow !text-[9px] mb-2.5">Account type - how should the engine guard this account?</div>
        <Segmented
          value={acctType}
          onChange={(k) => setAcctType(k as "personal" | "propfirm")}
          options={[
            { key: "personal", label: "Personal" },
            { key: "propfirm", label: "Prop firm" },
          ]}
        />
        {acctType === "propfirm" ? (
          <>
            <div className="mt-4 grid grid-cols-2 gap-x-3 gap-y-3">
              <div>
                <div className="eyebrow !text-[9px] mb-1.5">Daily drawdown %</div>
                <input className="input-mini" inputMode="decimal" value={propDaily} onChange={(e) => setPropDaily(e.target.value)} aria-label="Daily drawdown percent" />
              </div>
              <div>
                <div className="eyebrow !text-[9px] mb-1.5">Max total drawdown %</div>
                <input className="input-mini" inputMode="decimal" value={propTotal} onChange={(e) => setPropTotal(e.target.value)} aria-label="Max total drawdown percent" />
              </div>
              <div>
                <div className="eyebrow !text-[9px] mb-1.5">Profit target %</div>
                <input className="input-mini" inputMode="decimal" value={propTarget} onChange={(e) => setPropTarget(e.target.value)} aria-label="Profit target percent" />
              </div>
              <div>
                <div className="eyebrow !text-[9px] mb-1.5">Safety buffer %</div>
                <input className="input-mini" inputMode="decimal" value={propBuffer} onChange={(e) => setPropBuffer(e.target.value)} aria-label="Safety buffer percent" />
              </div>
              <div className="col-span-2">
                <div className="eyebrow !text-[9px] mb-1.5">Start balance $ (optional - for drawdown math)</div>
                <input className="input-mini" inputMode="decimal" value={propStart} onChange={(e) => setPropStart(e.target.value)} placeholder="0 = use your balance above" aria-label="Account start balance" />
              </div>
            </div>
            <p className="mt-3 text-[10.5px] leading-relaxed text-txt-faint">
              PROP MODE: the engine stops suggesting new signals once today's result reaches {propBuffer === "" ? 80 : 100 - (parseFloat(propBuffer) || 20)}% of your {propDaily || 5}% daily drawdown, and fully stops at the {(parseFloat(propTotal) || 10)}% total drawdown wall - so the firm's rule is never breached. Target: {propTarget || 8}%. Signals already tracking always run to completion.
            </p>
          </>
        ) : (
          <p className="mt-3 text-[10.5px] leading-relaxed text-txt-faint">
            Personal mode: the engine guards signals with the daily loss limit, session and market rules below. Switch to Prop firm to add challenge rules (daily drawdown, max drawdown, profit target).
          </p>
        )}
      </Glass>
      <p className="mb-1 mt-6 px-1 text-[11px] leading-relaxed text-txt-faint">Entry timeframes - the bot only enters on these</p>
      <p className="mb-3 mt-1 px-1 text-[11px] text-txt-faint">Signal filtering and guidance - the app never executes.</p>
      <Glass>
        <div className="eyebrow !text-[9px] mb-2.5">Entry timeframes - the bot only enters on these</div>
        <div className="grid grid-cols-4 gap-2">
          {TF_OPTIONS.map((tf) => (
            <button
              key={tf}
              onClick={() => toggleTf(tf)}
              aria-pressed={timeframes.includes(tf)}
              className={`tap min-h-[44px] rounded-2xl border px-2 py-2.5 text-[12px] font-bold tracking-wide ${
                timeframes.includes(tf) ? "chip-on" : "chip-off"
              }`}
            >
              {tf}
            </button>
          ))}
        </div>
        <p className="mt-2 text-[10px] leading-relaxed text-txt-faint">
          Signals already tracking always run to completion. 5M is not offered on the free data plan.
        </p>
        <Divider className="my-5" />
        <div className="grid grid-cols-2 gap-4">
          <Field label="Risk / trade %" value={riskPct} onChange={setRiskPct} />
          <Field label="Max daily loss %" value={maxLoss} onChange={setMaxLoss} />
          <Field label="Max signals / day" value={maxSignals} onChange={setMaxSignals} />
          <Field label="Min R:R (S1)" value={minRR} onChange={setMinRR} />
        </div>
        <Divider className="my-5" />
        <div className="eyebrow !text-[9px] mb-2.5">Sessions analyzed</div>
        <div className="grid grid-cols-4 gap-2">
          {SESSION_OPTIONS.map((sn: string) => (
            <button
              key={sn}
              onClick={() => toggleSession(sn)}
              aria-pressed={sessions.includes(sn)}
              className={`tap min-h-[44px] rounded-2xl border px-1 py-2.5 text-[11px] font-bold tracking-wide ${
                sessions.includes(sn) ? "chip-on" : "chip-off"
              }`}
            >
              {sn === "NewYork" ? "New York" : sn}
            </button>
          ))}
        </div>
        {sessions.length === 0 && (
          <p className="mt-2 text-[10px] leading-relaxed text-neg">
            No sessions on - no new signals will be generated.
          </p>
        )}
        {(risk.data.allowed_markets ?? []).length > 0 && (
          <>
            <div className="eyebrow !text-[9px] mb-2.5 mt-4">Per-market sessions</div>
            <div className="space-y-2">
              {(risk.data.allowed_markets ?? []).map((mk: string) => {
                const override = marketSessions[mk];
                return (
                  <div key={mk} className="glass-2 rounded-2xl px-3 py-2.5">
                    <div className="mb-1.5 flex items-center justify-between">
                      <span className="text-[11px] font-bold">{mk}</span>
                      {!override && <span className="text-[9px] text-txt-faint">follows global</span>}
                    </div>
                    <div className="grid grid-cols-4 gap-1.5">
                      {SESSION_OPTIONS.map((sn: string) => {
                        const on = override ? override.includes(sn) : sessions.includes(sn);
                        return (
                          <button
                            key={sn}
                            onClick={() => toggleMarketSession(mk, sn)}
                            aria-pressed={on}
                            className={`tap min-h-[36px] rounded-xl border px-1 py-1.5 text-[9.5px] font-bold ${
                              on ? "chip-on" : "chip-off"
                            }`}
                          >
                            {sn === "NewYork" ? "NY" : sn === "London" ? "LDN" : sn === "Asian" ? "ASIA" : "LATE"}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
            <p className="mt-2 text-[10px] leading-relaxed text-txt-faint">
              Tap to give a market its own session rules. Untick all four to follow the global sessions again. Custom hour windows and timezone are editable too (below).
            </p>
          </>
        )}
        <div className="mt-4">
          <Field label="Sessions timezone (IANA, e.g. Africa/Accra)" value={sessionTz} onChange={setSessionTz} />
        </div>
        <div className="eyebrow !text-[9px] mb-2.5 mt-4">Allowed markets</div>
        <div className="flex flex-wrap gap-2">
          {(risk.data.available_markets ?? risk.data.allowed_markets ?? []).map((mk: string) => (
            <button
              key={mk}
              onClick={() => toggleMk(mk)}
              aria-pressed={markets.includes(mk)}
              className={`tap min-h-[44px] rounded-2xl border px-3 py-2 text-[12px] font-bold tracking-wide ${
                markets.includes(mk) ? "chip-on" : "chip-off"
              }`}
            >
              {mk}
            </button>
          ))}
        </div>
        {markets.length === 0 && (
          <p className="mt-2 text-[10px] leading-relaxed text-neg">
            All markets off - no new signals will be generated.
          </p>
        )}
        <p className="mt-2 text-[10px] leading-relaxed text-txt-faint">
          Broker symbol suffixes (XAUUSDm, USTEC...) are resolved automatically on the VPS - configure them with the SYMBOL_ALIASES env on the bridge, not here.
        </p>
        <Divider className="my-5" />
        <div className="eyebrow !text-[9px] mb-2.5">Daily account walls (USD)</div>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Profit target $" value={profitTarget} onChange={setProfitTarget} />
          <Field label="Loss limit $" value={lossLimitUsd} onChange={setLossLimitUsd} />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <button onClick={() => setOnLossLimit("stop_entries")} aria-pressed={onLossLimit === "stop_entries"}
            className={`tap min-h-[44px] rounded-2xl border px-2 py-2 text-[11px] font-bold ${onLossLimit === "stop_entries" ? "chip-on" : "chip-off"}`}>
            Limit hit: stop new entries
          </button>
          <button onClick={() => setOnLossLimit("stop_and_close")} aria-pressed={onLossLimit === "stop_and_close"}
            className={`tap min-h-[44px] rounded-2xl border px-2 py-2 text-[11px] font-bold ${onLossLimit === "stop_and_close" ? "chip-on" : "chip-off"}`}>
            Limit hit: also close all
          </button>
        </div>
        <p className="mt-2 text-[10px] leading-relaxed text-txt-faint">
          When a wall is hit, the strategy keeps generating and recording signals - they arrive as EXTRA SIGNALS (manual decision), automatic entry is disabled. Resets at your sessions timezone midnight.
        </p>
        <button className="btn-primary mt-5 w-full" onClick={saveRisk}>
          <Sparkles size={14} /> Save settings
        </button>
      </Glass>

      {/* ---- strategies on/off (always visible; was buried in Strategy Manager) ---- */}
      <StrategiesCard />

      {/* ---- telegram phone alerts ---- */}
      <Eyebrow className="mt-9">Phone alerts - Telegram</Eyebrow>
      <p className="mb-3 mt-1 px-1 text-[11px] text-txt-faint">Signals, TP/SL hits and approvals delivered even when the app is closed.</p>
      <Glass>
        {(() => {
          const tg = info.data?.notifications?.telegram;
          const linked = !!me.data?.telegram_linked;
          const email = me.data?.email || "";
          if (!tg?.configured) {
            return <p className="text-[12px] text-txt-mid">Telegram bot not configured yet - add the bot token to enable phone alerts.</p>;
          }
          return (
            <>
              {linked ? (
                <div className="flex items-center gap-2.5 text-[12.5px] font-semibold text-pos">
                  <GlowDot tone="pos" size={7} pulse={false} /> Connected - alerts will arrive in your Telegram.
                </div>
              ) : (
                <ol className="space-y-2.5 text-[12px] leading-relaxed text-txt-mid">
                  <li><span className="num font-bold text-txt-hi">1.</span> Open <span className="font-semibold text-[var(--accent-cyan)]">t.me/{tg.bot_username || "your_bot"}</span> in Telegram</li>
                  <li><span className="num font-bold text-txt-hi">2.</span> Send this exact message: <span className="mt-1 block rounded-xl border border-[rgba(var(--warm-rgb),0.09)] bg-[rgba(6,11,26,0.6)] px-3 py-2 font-mono text-[11px] text-txt-hi">/start {email}</span></li>
                  <li><span className="num font-bold text-txt-hi">3.</span> Tap "Check again" below.</li>
                </ol>
              )}
              <div className="mt-4 flex gap-2.5">
                {!linked && (
                  <button className="btn-ghost flex-1" onClick={() => me.refresh()}>Check again</button>
                )}
                <button
                  className="btn-primary flex-1"
                  onClick={async () => {
                    try {
                      const r = await api.post<{ sent: boolean }>(endpoints.notificationTest, {});
                      flash(r.sent ? "Test alert sent - check your Telegram" : "Not linked yet - follow the steps above.");
                    } catch (e: any) { flash(e.message || "Failed"); }
                  }}
                >
                  <Send size={13} /> Send test alert
                </button>
              </div>
            </>
          );
        })()}
      </Glass>

      {/* ---- order execution (MT5 via VPS bridge) ---- */}
      <ExecutionCard />

      {/* ---- system ---- */}
      <Eyebrow className="mt-9">System</Eyebrow>
      <Glass className="mt-2 !py-1" pad={false}>
        <div className="px-5">
          <Row label="Market data" value={info.data?.market_data?.demo ? "Demo - historical replay" : "Live - Twelve Data"} tone={info.data?.market_data?.demo ? "text-warn" : "text-pos"} dot={info.data?.market_data?.demo ? "warn" : "pos"} />
          <StoredHistoryRow />
          <Divider />
          <Row label="AI provider" value={info.data?.ai?.provider === "xkiro" ? "XKiro" : "Grounded analyst"} dot="acc" />
          <Divider />
          <Row label="Database" value={info.data?.database?.firestore_active ? "Firestore (live)" : "Local - Firebase-ready"} dot="acc" />
        </div>
      </Glass>

      <div className="mt-6 grid grid-cols-2 gap-3">
        <Link to="/strategies" className="btn-ghost">Strategy Manager</Link>
        <Link to="/notifications" className="btn-ghost">Notifications</Link>
      </div>

      <p className="mt-8 pb-4 text-center text-[10px] leading-relaxed text-txt-faint">
        ForexMind AI is a research & signal agent. It analyzes markets, explains setups and tracks outcomes.<br />
        You manually enter trades on MT5. It never places orders and never guarantees profits.<br />
        <span className="text-txt-faint/60">v0.1.0 - Trade - Learn - Grow</span>
      </p>
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[9px] font-medium uppercase tracking-[0.14em] text-txt-faint">{label}</span>
      <input className="input !px-3.5 !py-2.5 text-[13px]" inputMode="decimal" value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function Row({ label, value, tone = "text-txt-mid", dot }: { label: string; value: string; tone?: string; dot?: "pos" | "warn" | "acc" }) {
  return (
    <div className="flex items-center justify-between py-3.5">
      <span className="text-[12.5px] text-txt-low">{label}</span>
      <span className={`flex items-center gap-2 text-[12px] font-medium ${tone}`}>
        {dot && <GlowDot tone={dot} size={5} pulse={false} />}
        {value}
      </span>
    </div>
  );
}

/** Real recorded 15M candle history per market (from the candle store). */
function StoredHistoryRow() {
  const [st, setSt] = useState<any>(null);
  useEffect(() => {
    let alive = true;
    api.get("/api/candles").then((d: any) => alive && setSt(d)).catch(() => {});
    const id = setInterval(() => api.get("/api/candles").then((d: any) => alive && setSt(d)).catch(() => {}), 60000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  const markets = st?.markets ? Object.values(st.markets) : [];
  const active = markets.filter((m: any) => m?.count > 0).length;
  return (
    <>
      <Divider />
      <Row
        label="Candle history"
        value={st ? `${(st.total ?? 0).toLocaleString()} candles - ${active}/${markets.length} markets` : "..."}
        dot="acc"
      />
    </>
  );
}

/** MT5 execution: mode chooser (off / manual PC / vps later) + kill switch. */
function ExecutionCard() {
  const st = usePolling<any>(() => api.get("/api/execution/status"), 20000);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [copied, setCopied] = useState(false);
  const s = st.data;
  const mode = s?.mode ?? "off";

  const setMode = async (m: string) => {
    if (busy || !s || m === mode) return;
    setBusy(true); setMsg("");
    try {
      await api.post("/api/execution/mode", { mode: m });
      setMsg(m === "manual" ? "Manual mode on - pair your PC below." :
             m === "off" ? "Execution off - signals are advisory only." : "VPS mode on.");
      st.refresh?.();
    } catch (e: any) {
      setMsg(e?.response?.data?.detail || "Could not switch mode - try again.");
    }
    setBusy(false);
  };

  const toggle = async () => {
    if (busy || !s) return;
    setBusy(true);
    try {
      await api.post("/api/execution/toggle", { enabled: !s.enabled });
      st.refresh?.();
    } catch { /* keep */ }
    setBusy(false);
  };

  const code = s?.pairing_code || "";
  const online = !!s?.connector_online;
  const seen = s?.connector_last_seen ? new Date(s.connector_last_seen * 1000).toLocaleTimeString() : null;

  return (
    <>
      <Eyebrow className="mt-9">Order execution - MT5</Eyebrow>
      <Glass className="mt-2">
        <Segmented options={[{key:"off",label:"Off"},{key:"manual",label:"Manual PC"},{key:"vps",label:"VPS"}]} value={mode} onChange={setMode} />

        {mode === "manual" && (
          <div className="mt-4">
            <div className={`flex items-center justify-between rounded-2xl border px-4 py-3 ${online ? "border-[rgba(var(--p-rgb),0.3)] bg-[rgba(var(--p-rgb),0.07)]" : "border-[rgba(var(--warm-rgb),0.08)] bg-[rgba(var(--warm-rgb),0.035)]"}`}>
              <div className="flex items-center gap-2">
                <GlowDot tone={online ? "pos" : "warn"} size={7} pulse={online} />
                <div>
                  <div className="text-[12.5px] font-semibold text-txt-hi">
                    {online ? "PC connected" : seen ? "PC offline" : "Waiting for your PC"}
                  </div>
                  <div className="text-[10.5px] text-txt-faint">
                    {s?.connector_machine || "Run the connector on the PC with MT5"}
                    {seen ? ` - last seen ${seen}` : ""}
                  </div>
                </div>
              </div>
              {s?.account && (
                <div className="text-right">
                  <div className="num text-[13px] font-bold text-txt-hi">{s.account.balance}</div>
                  <div className="text-[9px] text-txt-faint">{s.account.currency} balance</div>
                </div>
              )}
            </div>

            <div className="mt-3 rounded-2xl border border-[rgba(var(--warm-rgb),0.08)] bg-[rgba(var(--warm-rgb),0.035)] p-4">
              <div className="text-[10px] uppercase tracking-wide text-txt-faint">Pairing code</div>
              <div className="mt-1 flex items-center justify-between">
                <span className="num text-[18px] font-bold tracking-wider text-txt-hi">{code || "..."}</span>
                <button className="tap rounded-full border border-[rgba(var(--warm-rgb),0.12)] px-3 py-1.5 text-[11px] text-txt-mid"
                  onClick={() => { try { navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch { /* */ } }}>
                  {copied ? "Copied" : "Copy"}
                </button>
              </div>
              <ol className="mt-3 space-y-1.5 text-[11px] leading-relaxed text-txt-mid">
                <li>1. On the PC: open MT5 and log in (demo account).</li>
                <li>2. Download the ForexMind connector folder (mt5-connector) to that PC.</li>
                <li>3. Run: <span className="num">pip install -r requirements.txt</span></li>
                <li>4. Set <span className="num">PAIRING_CODE={code || "your code"}</span> and run <span className="num">python connector.py</span></li>
              </ol>
              <p className="mt-2 text-[10px] leading-relaxed text-txt-faint">
                The connector dials out to the cloud - no router changes needed. Orders expire safely if the PC is off.
              </p>
            </div>
          </div>
        )}

        {mode === "vps" && s?.bridge_configured && (
          <div className="mt-4 flex items-center justify-between rounded-2xl border border-[rgba(var(--warm-rgb),0.08)] bg-[rgba(var(--warm-rgb),0.035)] px-4 py-3">
            <div className="flex items-center gap-2">
              <GlowDot tone={s.bridge_online ? "pos" : "warn"} size={7} pulse={s.bridge_online} />
              <span className="text-[12.5px] font-semibold text-txt-hi">{s.bridge_online ? "Bridge online" : "Bridge offline"}</span>
            </div>
            {s.account && <span className="num text-[13px] font-bold text-txt-hi">{s.account.balance} {s.account.currency}</span>}
          </div>
        )}

        {mode === "off" && (
          <p className="mt-4 text-[12px] leading-relaxed text-txt-mid">
            Signals are advisory only - nothing is placed. Pick <span className="font-semibold text-txt-hi">Manual (this PC)</span> to let a running MT5 execute them for you, or <span className="font-semibold text-txt-hi">VPS bridge</span> after the VPS setup.
          </p>
        )}

        {mode !== "off" && (
          <>
            <div className="mt-3 grid grid-cols-3 gap-2 text-center">
              <div className="rounded-xl border border-[rgba(var(--warm-rgb),0.07)] bg-[rgba(var(--warm-rgb),0.035)] py-2">
                <div className="text-[9px] uppercase tracking-wide text-txt-faint">Today</div>
                <div className="num text-[13px] font-bold text-txt-hi">{s?.trades_today ?? 0}/{s?.max_per_day ?? 6}</div>
              </div>
              <div className="rounded-xl border border-[rgba(var(--warm-rgb),0.07)] bg-[rgba(var(--warm-rgb),0.035)] py-2">
                <div className="text-[9px] uppercase tracking-wide text-txt-faint">Risk cap</div>
                <div className="num text-[13px] font-bold text-txt-hi">{s?.risk_cap_pct ?? 1}%</div>
              </div>
              <div className="rounded-xl border border-[rgba(var(--warm-rgb),0.07)] bg-[rgba(var(--warm-rgb),0.035)] py-2">
                <div className="text-[9px] uppercase tracking-wide text-txt-faint">TP level</div>
                <div className="num text-[13px] font-bold text-txt-hi">TP{s?.tp_level ?? 2}</div>
              </div>
            </div>
            <button className={`mt-3 w-full ${s?.enabled ? "btn-ghost" : "btn-primary"}`} disabled={busy} onClick={toggle}>
              {s?.enabled ? "STOP auto-trading (kill switch)" : "RESUME auto-trading"}
            </button>
            <p className="mt-2 text-center text-[10px] text-txt-faint">
              {s?.enabled ? "Kill switch stops new orders instantly - open MT5 positions stay managed by their SL/TP." : "Auto-trading is currently stopped."}
            </p>
          </>
        )}
        {msg && <p className="mt-2 text-center text-[11px] font-semibold text-[var(--accent-green)]">{msg}</p>}
      </Glass>
    </>
  );
}


/** Strategies ON/OFF - always visible in Settings (mirrors Strategy Manager status). */
function StrategiesCard() {
  const list = usePolling<any>(() => api.get(endpoints.strategies), 12000);
  const [busy, setBusy] = useState("");
  const [toast, setToast] = useState("");
  const flash = (m: string) => {
    setToast(m);
    setTimeout(() => setToast(""), 3500);
  };
  const items = (list.data?.strategies ?? []) as any[];
  const toggle = async (s: any) => {
    const turningOn = s.status !== "ACTIVE";
    setBusy(s.id);
    try {
      await api.patch(endpoints.strategy(s.id), { status: turningOn ? "ACTIVE" : "PAUSED" });
      flash(`${s.short_name}: ${turningOn ? "ON - will generate new signals" : "OFF - no new signals"}.`);
      await list.refresh();
    } catch {
      flash("Could not update - check connection and try again.");
    }
    setBusy("");
  };
  return (
    <>
      <Eyebrow className="mt-9">Strategies - on / off</Eyebrow>
      <p className="mb-3 mt-1 px-1 text-[11px] text-txt-faint">
        Turn each strategy on or off. OFF stops new signals only - signals already tracking always run to completion.
      </p>
      {toast && (
        <div className="glass-2 mb-3 flex items-center gap-2.5 px-4 py-3 text-[12px] font-medium text-acc-cyan">
          <GlowDot tone="acc" size={6} pulse={false} /> {toast}
        </div>
      )}
      <Glass pad={false} className="divide-y divide-[rgba(var(--warm-rgb),0.06)] !p-0">
        {list.loading && !list.data ? (
          <div className="flex justify-center py-6"><Spinner /></div>
        ) : items.length === 0 ? (
          <div className="px-5 py-5 text-[12px] text-txt-low">No strategies registered yet.</div>
        ) : (
          items.map((s: any) => {
            const on = s.status === "ACTIVE";
            return (
              <div key={s.id} className={`flex items-center gap-4 px-5 py-4 ${busy === s.id ? "opacity-50" : ""}`}>
                <GlowDot tone={on ? "pos" : "warn"} size={7} pulse={on} />
                <div className="min-w-0 flex-1">
                  <div className="text-[13.5px] font-medium tracking-tight text-txt-hi">{s.short_name}</div>
                  <div className={`mt-0.5 text-[11px] font-semibold ${on ? "text-pos" : "text-txt-faint"}`}>
                    {on ? "ON - generating signals" : "OFF - paused"}
                  </div>
                </div>
                <button
                  role="switch"
                  aria-checked={on}
                  aria-label={`${s.short_name} on/off`}
                  disabled={busy === s.id}
                  onClick={() => toggle(s)}
                  className="tap flex min-h-[44px] items-center"
                >
                  <span
                    className={`relative block h-[30px] w-[56px] rounded-full transition-all duration-300 ${
                      on ? "shadow-[0_0_16px_rgba(var(--p2-rgb),0.4)]" : ""
                    }`}
                    style={{ background: on ? "linear-gradient(120deg, var(--c-p1) 0%, var(--c-p3) 100%)" : "rgba(255,255,255,0.12)" }}
                  >
                    <span
                      className="absolute top-[3px] h-[24px] w-[24px] rounded-full bg-white shadow transition-all duration-300"
                      style={{ left: on ? "29px" : "3px" }}
                    />
                  </span>
                </button>
              </div>
            );
          })
        )}
      </Glass>
      <Link to="/strategies" className="btn-ghost mt-3 w-full">Strategy Manager - versions &amp; rollback</Link>
    </>
  );
}
