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
  useEffect(() => {
    if (risk.data) {
      setRiskPct(String(risk.data.risk_per_trade_pct));
      setMaxLoss(String(risk.data.max_daily_loss_pct));
      setMaxSignals(String(risk.data.max_signals_per_day));
      setMinRR(String(risk.data.min_rr));
    }
  }, [risk.data]);

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
    await api.patch(endpoints.settings, {
      risk_per_trade_pct: parseFloat(riskPct),
      max_daily_loss_pct: parseFloat(maxLoss),
      max_signals_per_day: parseInt(maxSignals),
      min_rr: parseFloat(minRR),
    });
    flash("Risk controls updated.");
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
      <Eyebrow>Agent objective</Eyebrow>
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
      <p className="mb-3 mt-1 px-1 text-[11px] text-txt-faint">Signal filtering and guidance - the app never executes.</p>
      <Glass>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Risk / trade %" value={riskPct} onChange={setRiskPct} />
          <Field label="Max daily loss %" value={maxLoss} onChange={setMaxLoss} />
          <Field label="Max signals / day" value={maxSignals} onChange={setMaxSignals} />
          <Field label="Min R:R (S1)" value={minRR} onChange={setMinRR} />
        </div>
        <Divider className="my-5" />
        <div className="eyebrow !text-[9px] mb-2.5">Sessions analyzed</div>
        <div className="flex flex-wrap gap-2">
          {(risk.data.sessions ?? []).map((s: string) => (
            <Pill key={s} tone="cyan">{s}</Pill>
          ))}
        </div>
        <div className="eyebrow !text-[9px] mb-2.5 mt-4">Allowed markets</div>
        <div className="flex flex-wrap gap-2">
          {(risk.data.allowed_markets ?? []).map((mk: string) => (
            <Pill key={mk} tone="neutral">{mk}</Pill>
          ))}
        </div>
        <button className="btn-primary mt-5 w-full" onClick={saveRisk}>
          <Sparkles size={14} /> Save settings
        </button>
      </Glass>

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
                  <li><span className="num font-bold text-txt-hi">2.</span> Send this exact message: <span className="mt-1 block rounded-xl border border-white/[0.08] bg-[rgba(6,11,26,0.6)] px-3 py-2 font-mono text-[11px] text-txt-hi">/start {email}</span></li>
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
                      flash(r.sent ? "Test alert sent - check your Telegram 📲" : "Not linked yet - follow the steps above.");
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
            <div className={`flex items-center justify-between rounded-2xl border px-4 py-3 ${online ? "border-[rgba(47,217,138,0.3)] bg-[rgba(47,217,138,0.07)]" : "border-white/[0.07] bg-white/[0.02]"}`}>
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

            <div className="mt-3 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4">
              <div className="text-[10px] uppercase tracking-wide text-txt-faint">Pairing code</div>
              <div className="mt-1 flex items-center justify-between">
                <span className="num text-[18px] font-bold tracking-wider text-txt-hi">{code || "..."}</span>
                <button className="tap rounded-full border border-white/[0.1] px-3 py-1.5 text-[11px] text-txt-mid"
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
          <div className="mt-4 flex items-center justify-between rounded-2xl border border-white/[0.07] bg-white/[0.02] px-4 py-3">
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
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] py-2">
                <div className="text-[9px] uppercase tracking-wide text-txt-faint">Today</div>
                <div className="num text-[13px] font-bold text-txt-hi">{s?.trades_today ?? 0}/{s?.max_per_day ?? 6}</div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] py-2">
                <div className="text-[9px] uppercase tracking-wide text-txt-faint">Risk cap</div>
                <div className="num text-[13px] font-bold text-txt-hi">{s?.risk_cap_pct ?? 1}%</div>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] py-2">
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

