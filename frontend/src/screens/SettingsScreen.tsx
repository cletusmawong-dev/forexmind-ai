import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Sparkles } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import { DemoTag, Divider, Eyebrow, Glass, GlowDot, Pill, Spinner } from "../components/ui";
import { Logo } from "../components/Logo";

export function SettingsScreen() {
  const goals = usePolling<any>(() => api.get(endpoints.goals), 10000);
  const risk = usePolling<any>(() => api.get(endpoints.settings), 10000);
  const info = usePolling<any>(() => api.get(endpoints.systemInfo), 30000);
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

  if (!goals.data || !risk.data) return <Spinner label="Loading settings…" />;

  return (
    <div className="animate-fadeUp">
      <header className="mb-7 flex items-center justify-between">
        <Logo size={30} />
        <DemoTag />
      </header>

      {toast && <div className="glass-2 mb-5 px-4 py-3 text-[12px] font-medium text-acc-cyan">{toast}</div>}

      {/* ---- objective ---- */}
      <Eyebrow>Agent objective</Eyebrow>
      <p className="mb-3 mt-1 px-1 text-[11px] text-txt-faint">Guides the research focus — never a command to trade.</p>
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
      <p className="mb-3 mt-1 px-1 text-[11px] text-txt-faint">Signal filtering and guidance — the app never executes.</p>
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

      {/* ---- system ---- */}
      <Eyebrow className="mt-9">System</Eyebrow>
      <Glass className="mt-2 !py-1" pad={false}>
        <div className="px-5">
          <Row label="Market data" value={info.data?.market_data?.demo ? "Demo · historical replay" : "Live"} tone="text-warn" dot="warn" />
          <Divider />
          <Row label="AI provider" value={info.data?.ai?.provider === "xkiro" ? "XKiro" : "Grounded analyst"} dot="acc" />
          <Divider />
          <Row label="Database" value={info.data?.database?.firestore_active ? "Firestore (live)" : "Local · Firebase-ready"} dot="acc" />
          <Divider />
          <Row label="Order execution" value="Disabled · by design" tone="text-pos" dot="pos" />
        </div>
      </Glass>

      <div className="mt-6 grid grid-cols-2 gap-3">
        <Link to="/strategies" className="btn-ghost">Strategy Manager</Link>
        <Link to="/notifications" className="btn-ghost">Notifications</Link>
      </div>

      <p className="mt-8 pb-4 text-center text-[10px] leading-relaxed text-txt-faint">
        ForexMind AI is a research & signal agent. It analyzes markets, explains setups and tracks outcomes.<br />
        You manually enter trades on MT5. It never places orders and never guarantees profits.<br />
        <span className="text-txt-faint/60">v0.1.0 · Trade · Learn · Grow</span>
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
