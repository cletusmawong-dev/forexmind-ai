import { useState } from "react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import { ActionButton, Eyebrow, Glass, Pill, Segmented } from "./ui";
import { useAsyncAction } from "../lib/useAsyncAction";

/* ============================================================================
   FOREXMIND 3.0 intelligence surfaces (stages 5-9): Evidence, research tools,
   decision replay, TCA, kill-switch, incidents, security.
   Every control uses the shared async-action pattern: real loading / success /
   failure states - never fake success (P16 / spec 55).
   ========================================================================== */

const STATE_TONE: Record<string, "pos" | "warn" | "danger" | "cyan"> = {
  STRONGER: "pos", SUPPORTED: "cyan", PRELIMINARY: "warn",
  INSUFFICIENT: "warn", CONTRADICTED: "danger",
};

export function EvidencePanel() {
  const ev = usePolling<{ evidence: any[]; states: Record<string, number> }>(
    () => api.get(endpoints.evidence), 20000);
  const gen = useAsyncAction();
  const [openId, setOpenId] = useState<string | null>(null);

  return (
    <div className="animate-fadeUp">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <Eyebrow>Evidence Engine</Eyebrow>
          <p className="mt-1 text-[11.5px] text-txt-low">
            How much the system actually trusts each conclusion - deterministic,
            independent of AI confidence. Contradictions are never hidden.
          </p>
        </div>
        <ActionButton label="Recompute evidence" busy={gen.busy}
          onRun={async () => {
            await gen.run(() => api.post(endpoints.evidenceGenerate, {}),
              "recomputed");
            ev.refresh();
            return "Evidence recomputed";
          }} />
      </div>

      {ev.data && Object.values(ev.data.states ?? {}).some((v) => v > 0) && (
        <div className="mb-4 flex flex-wrap gap-2">
          {Object.entries(ev.data.states).map(([k, v]) => v > 0 ? (
            <Pill key={k} tone={(STATE_TONE[k] ?? "warn") as any}>{k}: {v}</Pill>
          ) : null)}
        </div>
      )}

      <div className="space-y-3">
        {(ev.data?.evidence ?? []).map((e) => (
          <Glass key={e.evidence_id} className="px-5 py-4">
            <button className="w-full text-left" onClick={() => setOpenId(openId === e.evidence_id ? null : e.evidence_id)}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Pill tone={(STATE_TONE[e.state] ?? "warn") as any}>{e.state}</Pill>
                  <span className="text-[12.5px] font-medium text-txt-hi">{e.subject_id}</span>
                </div>
                <span className="num text-[12px] font-bold text-txt-mid">score {e.score}</span>
              </div>
              <p className="mt-1.5 text-[11px] text-txt-mid">{e.conclusion}</p>
              <div className="mt-1.5 flex flex-wrap gap-3 text-[10px] text-txt-faint">
                <span>n={e.sample_size}</span>
                {e.currently_supported !== undefined && (
                  <span>currently supported: {e.currently_supported ? "yes" : "no"}</span>)}
                {e.evidence_age_days !== null && <span>age {e.evidence_age_days}d</span>}
                {(e.contradictions ?? []).length > 0 && (
                  <span className="text-[var(--accent-red)]">{e.contradictions.length} contradiction(s)</span>)}
              </div>
            </button>
            {openId === e.evidence_id && (
              <div className="mt-3 space-y-2.5 border-t border-[rgba(var(--warm-rgb),0.08)] pt-3">
                {e.score_components && (
                  <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
                    {Object.entries(e.score_components).map(([k, v]) => (
                      <div key={k} className="rounded-lg border border-[rgba(var(--warm-rgb),0.07)] px-2 py-1.5 text-center">
                        <div className="text-[8.5px] uppercase tracking-wide text-txt-faint">{k}</div>
                        <div className="num text-[11.5px] font-bold text-txt-mid">{String(v)}</div>
                      </div>
                    ))}
                  </div>
                )}
                {(e.contradictions ?? []).length > 0 && (
                  <div>
                    <div className="text-[9.5px] font-semibold uppercase tracking-wide text-[var(--accent-red)]">Contradicting evidence</div>
                    {e.contradictions.map((x: any, i: number) => (
                      <p key={i} className="mt-1 text-[11px] text-txt-mid">- {x.detail}</p>
                    ))}
                  </div>
                )}
                {(e.supporting_factors ?? []).length > 0 && (
                  <div>
                    <div className="text-[9.5px] font-semibold uppercase tracking-wide text-pos">Supporting evidence</div>
                    {e.supporting_factors.map((x: any, i: number) => (
                      <p key={i} className="mt-1 text-[11px] text-txt-mid">- {x.detail}</p>
                    ))}
                  </div>
                )}
                {(e.missing_evidence ?? []).length > 0 && (
                  <p className="text-[10.5px] text-txt-faint">Missing: {e.missing_evidence.join(", ")}</p>
                )}
                {e.calibration && (
                  <p className="text-[10.5px] text-txt-faint">Calibration: {e.calibration}</p>
                )}
              </div>
            )}
          </Glass>
        ))}
        {ev.data?.evidence?.length === 0 && (
          <Glass className="px-5 py-8 text-center text-[12px] text-txt-low">
            No evidence objects yet - recompute after signals complete.
          </Glass>
        )}
      </div>
    </div>
  );
}

export function ResearchToolsPanel() {
  const [out, setOut] = useState<{ label: string; body: any } | null>(null);
  const act = useAsyncAction();
  const [q, setQ] = useState("");
  const ask = useAsyncAction();
  const strategies = ["strategy_2_ema_atr", "strategy_1_zero_lag"];

  const run = async (label: string, fn: () => Promise<any>): Promise<string> => {
    try {
      const body = await act.run(fn, "ok");
      setOut({ label, body });
    } catch { /* failure surfaced by act.error */ }
    return label;
  };

  return (
    <div className="animate-fadeUp space-y-4">
      <Glass className="px-5 py-4">
        <Eyebrow>Research tools</Eyebrow>
        <p className="mt-1 text-[11px] text-txt-low">
          Monte Carlo and stress runs are clearly-labeled simulations on your real
          trade history - never predictions. Blocked-trade analysis shows what
          filters cost or saved.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {strategies.map((sid) => (
            <ActionButton key={sid} tone="pos" busy={act.busy} label={`Monte Carlo: ${sid.includes("zero") ? "S1" : "S2"}`}
              onRun={() => run(`Monte Carlo - ${sid}`,
                () => api.post(endpoints.montecarlo, { strategy_id: sid }))} />
          ))}
          {strategies.map((sid) => (
            <ActionButton key={sid + "-s"} busy={act.busy} label={`Stress: ${sid.includes("zero") ? "S1" : "S2"}`}
              onRun={() => run(`Synthetic stress - ${sid}`,
                () => api.post(endpoints.stress, { strategy_id: sid }))} />
          ))}
          <ActionButton busy={act.busy} label="Calibration"
            onRun={() => run("Probability calibration", () => api.get(endpoints.calibration))} />
          <ActionButton busy={act.busy} label="Blocked trades"
            onRun={() => run("Why didn't we trade", () => api.get(endpoints.blocked))} />
          <ActionButton busy={act.busy} label="Shadow run"
            onRun={() => run("Shadow cycle", () => api.post(endpoints.shadowRun, {}))} />
        </div>
        {act.state === "error" && act.message && (
          <p className="mt-2 text-[11px] text-[var(--accent-red)]">{act.message}</p>
        )}
      </Glass>

      <Glass className="px-5 py-4">
        <Eyebrow>Ask your trade history</Eyebrow>
        <div className="mt-2 flex gap-2">
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder='e.g. "show me every strategy 2 XAUUSD loss"'
            className="min-w-0 flex-1 rounded-lg border border-[rgba(var(--warm-rgb),0.14)] bg-transparent px-3 py-2 text-[12px] text-txt-hi outline-none focus:border-[rgba(var(--p-rgb),0.4)]" />
          <ActionButton label="Ask" busy={ask.busy}
            onRun={async () => {
              const body = await ask.run(() => api.post(endpoints.ask, { question: q }), "asked");
              setOut({ label: `Answer - matched ${body?.matched ?? 0} signals`, body });
              return "Done";
            }} />
        </div>
        {ask.state === "error" && ask.message && (
          <p className="mt-2 text-[11px] text-[var(--accent-red)]">{ask.message}</p>
        )}
      </Glass>

      {out && (
        <Glass className="px-5 py-4">
          <Eyebrow>{out.label}</Eyebrow>
          <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-words text-[10.5px] leading-relaxed text-txt-mid">
            {JSON.stringify(out.body, null, 2)}
          </pre>
        </Glass>
      )}
    </div>
  );
}

export function ReplayPanel({ signalId }: { signalId: string }) {
  const replay = usePolling<any>(() => api.get(endpoints.replay(signalId)), 30000);
  if (!replay.data) return null;
  const r = replay.data;
  const chain = Array.isArray(r.execution_chain) ? r.execution_chain : [];
  return (
    <Glass className="mt-4 px-5 py-4">
      <Eyebrow>Decision replay - what the system knew at the time</Eyebrow>
      <p className="mt-1 text-[10.5px] text-txt-faint">{r.honesty}</p>
      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <div>
          <div className="text-[9.5px] font-semibold uppercase tracking-wide text-txt-faint">Known at signal time</div>
          <div className="mt-1.5 space-y-1 text-[11px] text-txt-mid">
            <div>Entry {String(r.known_at_time?.entry ?? "-")} - SL {String(r.known_at_time?.sl ?? "-")}</div>
            <div>Strategy: {String(r.known_at_time?.strategy ?? "-")}</div>
            {r.known_at_time?.market_conditions && (
              <div>Conditions: {JSON.stringify(r.known_at_time.market_conditions)}</div>)}
          </div>
        </div>
        <div>
          <div className="text-[9.5px] font-semibold uppercase tracking-wide text-txt-faint">Outcome (later info - kept separate)</div>
          <div className="mt-1.5 space-y-1 text-[11px] text-txt-mid">
            <div>{String(r.later_outcome?.outcome ?? "-")} - {String(r.later_outcome?.r_multiple ?? "-")}R</div>
            {r.later_outcome?.mt5 && <div>Ticket #{r.later_outcome.mt5.ticket} - ${r.later_outcome.mt5.pl}</div>}
          </div>
        </div>
      </div>
      {chain.length > 0 && (
        <div className="mt-3 border-t border-[rgba(var(--warm-rgb),0.08)] pt-3">
          <div className="text-[9.5px] font-semibold uppercase tracking-wide text-txt-faint">Execution chain</div>
          <div className="mt-1.5 space-y-1">
            {chain.map((e: any, i: number) => (
              <div key={i} className="flex items-center gap-2 text-[11px] text-txt-mid">
                <Pill tone={e.stage === "CONFIRMED" ? "pos" : e.stage === "FAILED" ? "danger" : "warn"}>{e.stage}</Pill>
                <span>{String(e.kind ?? "")} {e.latency_ms ? `- ${e.latency_ms}ms` : ""} {e.detail ? `- ${e.detail}` : ""}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Glass>
  );
}

export function TcaPanel() {
  const tca = usePolling<any>(() => api.get(endpoints.tca), 30000);
  const d = tca.data;
  if (!d) return null;
  const bi = d.broker_intelligence ?? {};
  return (
    <Glass className="px-5 py-4">
      <Eyebrow>Transaction cost analysis - real fills only</Eyebrow>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div><div className="text-[9px] uppercase tracking-wide text-txt-faint">Avg slippage</div>
          <div className="num text-[14px] font-bold text-txt-hi">{d.slippage?.avg_signed_slippage ?? "n/a"}</div></div>
        <div><div className="text-[9px] uppercase tracking-wide text-txt-faint">Fills measured</div>
          <div className="num text-[14px] font-bold text-txt-hi">{d.slippage?.measured_n ?? 0}</div></div>
        <div><div className="text-[9px] uppercase tracking-wide text-txt-faint">Reject rate</div>
          <div className="num text-[14px] font-bold text-txt-hi">{bi.reject_rate_pct ?? "-"}%</div></div>
        <div><div className="text-[9px] uppercase tracking-wide text-txt-faint">Avg latency</div>
          <div className="num text-[14px] font-bold text-txt-hi">{bi.latency_ms_avg ?? "-"}ms</div></div>
      </div>
      <p className="mt-2 text-[10px] text-txt-faint">{d.costs?.note}</p>
    </Glass>
  );
}

const LEVELS = ["L0 normal", "L1 stop entries", "L2 stop strategy", "L3 stop instrument", "L4 stop automated"];

export function KillSwitchPanel() {
  const ks = usePolling<any>(() => api.get(endpoints.killswitch), 15000);
  const act = useAsyncAction();
  const [closeConfirm, setCloseConfirm] = useState(false);
  const d = ks.data;

  return (
    <Glass className="px-5 py-4">
      <div className="flex items-center justify-between">
        <Eyebrow className="!mb-0">Kill switch</Eyebrow>
        <Pill tone={d?.level ? "danger" : "pos"}>{d?.name ?? "..."}</Pill>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {LEVELS.map((label, i) => (
          <ActionButton key={label} busy={act.busy} tone={d?.level === i ? "danger" : undefined}
            label={label}
            onRun={async () => {
              await act.run(() => api.post(endpoints.killswitch,
                { level: i, reason: `admin set L${i}` }), "set");
              ks.refresh();
              return `Kill switch at level ${i}`;
            }} />
        ))}
      </div>
      <div className="mt-4 border-t border-[rgba(var(--warm-rgb),0.08)] pt-3">
        {!closeConfirm ? (
          <button onClick={() => setCloseConfirm(true)}
            className="rounded-lg border border-[rgba(var(--neg-rgb),0.4)] px-3 py-1.5 text-[11px] font-semibold text-[var(--accent-red)] hover:bg-[rgba(var(--neg-rgb),0.08)]">
            Emergency close-all (L5)
          </button>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-semibold text-[var(--accent-red)]">
              Close ALL open positions and stop automation. Are you sure?
            </span>
            <ActionButton label="Yes, close everything" tone="danger" busy={act.busy}
              onRun={async () => {
                await act.run(() => api.post(endpoints.killswitchCloseAll, { confirm: true }), "done");
                setCloseConfirm(false);
                ks.refresh();
                return "Emergency close-all executed";
              }} />
            <button onClick={() => setCloseConfirm(false)} className="text-[11px] text-txt-faint underline">Cancel</button>
          </div>
        )}
      </div>
      {act.state === "error" && act.message && (
          <p className="mt-2 text-[11px] text-[var(--accent-red)]">{act.message}</p>
        )}
    </Glass>
  );
}

export function IncidentsPanel() {
  const inc = usePolling<{ incidents: any[]; open: number }>(() => api.get(endpoints.incidents), 20000);
  const act = useAsyncAction();
  return (
    <Glass className="px-5 py-4">
      <div className="flex items-center justify-between">
        <Eyebrow className="!mb-0">Incident center</Eyebrow>
        {inc.data && <Pill tone={inc.data.open ? "warn" : "pos"}>{inc.data.open} open</Pill>}
      </div>
      <div className="mt-3 space-y-2">
        {(inc.data?.incidents ?? []).slice(0, 8).map((i) => (
          <div key={i.id} className="rounded-xl border border-[rgba(var(--warm-rgb),0.08)] px-3 py-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Pill tone={i.severity === "CRITICAL" ? "danger" : i.severity === "WARNING" ? "warn" : "cyan"}>{i.severity}</Pill>
                <span className="text-[11.5px] font-medium text-txt-hi">{i.kind}</span>
                <span className="text-[10px] text-txt-faint">{i.subject} - x{i.occurrences}</span>
              </div>
              <span className="text-[9.5px] text-txt-faint">{i.status}</span>
            </div>
            <p className="mt-1 text-[10.5px] text-txt-mid">{i.detail}</p>
            {i.commander_report?.probable_cause && (
              <p className="mt-1 text-[10.5px] text-txt-faint">AI commander: {i.commander_report.probable_cause} (advice only)</p>
            )}
            {i.status !== "RESOLVED" && (
              <div className="mt-2 flex gap-2">
                {i.status === "DETECTED" && (
                  <ActionButton label="Acknowledge" busy={act.busy}
                    onRun={async () => { await act.run(() => api.post(`/api/incidents/${i.id}/ack`, {}), "ack"); inc.refresh(); return "Acknowledged"; }} />
                )}
                <ActionButton label="AI investigate" busy={act.busy}
                  onRun={async () => { await act.run(() => api.post(`/api/incidents/${i.id}/investigate`, {}), "inv"); inc.refresh(); return "Commander report added"; }} />
                <ActionButton label="Resolve" tone="pos" busy={act.busy}
                  onRun={async () => { await act.run(() => api.post(`/api/incidents/${i.id}/resolve`, { note: "resolved from admin" }), "res"); inc.refresh(); return "Resolved"; }} />
              </div>
            )}
          </div>
        ))}
        {inc.data?.incidents?.length === 0 && (
          <p className="py-4 text-center text-[12px] text-txt-low">No incidents - all clear.</p>
        )}
      </div>
      {act.state === "error" && act.message && (
          <p className="mt-2 text-[11px] text-[var(--accent-red)]">{act.message}</p>
        )}
    </Glass>
  );
}

export function SecurityPanel() {
  const sec = usePolling<any>(() => api.get(endpoints.security), 30000);
  const d = sec.data;
  if (!d) return null;
  return (
    <Glass className="px-5 py-4">
      <Eyebrow>Security center</Eyebrow>
      <div className="mt-3 space-y-2 text-[11.5px]">
        <div className="flex items-center justify-between">
          <span className="text-txt-mid">JWT secret</span>
          <Pill tone={d.jwt?.configured ? "pos" : "danger"}>
            {d.jwt?.configured ? "configured" : "DEV FALLBACK - HIGH RISK"}
          </Pill>
        </div>
        {!d.jwt?.configured && <p className="text-[10.5px] text-[var(--accent-red)]">{d.jwt?.risk}</p>}
        <div className="flex items-center justify-between">
          <span className="text-txt-mid">Telegram bot</span>
          <Pill tone={d.telegram?.configured ? "pos" : "warn"}>{d.telegram?.configured ? "configured" : "missing"}</Pill>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-txt-mid">Audit entries</span>
          <span className="num font-bold text-txt-hi">{d.audit_entries}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-txt-mid">Admins / suspended</span>
          <span className="num font-bold text-txt-hi">{d.admin_users} / {d.suspended_users}</span>
        </div>
        <p className="pt-1 text-[10px] text-txt-faint">{d.secret_policy}</p>
      </div>
    </Glass>
  );
}
