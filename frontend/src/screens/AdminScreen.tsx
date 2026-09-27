import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, RefreshCw, Shield, ShieldAlert, OctagonX } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import { Empty, Glass, PageHeader, Pill, SectionHeader, Spinner, StatusDot } from "../components/ui";
import { shortAgo } from "../lib/format";

const PERMISSIONS = ["locked", "setup", "enabled"] as const;

type AdminUser = {
  id: string; email?: string; display_name?: string; role: string;
  status: string; trading_permission: string; telegram_linked: boolean; createdAt?: string;
};

function num(v: any, d = 0): number { return typeof v === "number" ? v : d; }

/** One admin action button with its own loading + result state. Never silent. */
function ActionBtn({ label, busy, tone = "neutral", onRun }: {
  label: string; busy: boolean; tone?: "neutral" | "danger" | "pos"; onRun: () => Promise<string>;
}) {
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  return (
    <span className="inline-flex flex-col gap-0.5">
      <button
        disabled={busy}
        onClick={async () => {
          setMsg(null);
          try { setMsg({ ok: true, text: await onRun() }); }
          catch (e: any) { setMsg({ ok: false, text: e?.message || "failed" }); }
        }}
        className={`tap rounded-lg border px-2.5 py-1.5 text-[11px] font-medium transition disabled:opacity-50 ${
          tone === "danger"
            ? "border-red-500/30 text-red-300 hover:bg-red-500/10"
            : tone === "pos"
            ? "border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10"
            : "border-[rgba(var(--p-rgb),0.25)] text-txt-mid hover:bg-[rgba(var(--p-rgb),0.08)]"
        }`}
      >
        {busy ? "…" : label}
      </button>
      {msg && (
        <span className={`max-w-[220px] text-[10px] leading-tight ${msg.ok ? "text-emerald-300/80" : "text-red-300/90"}`}>
          {msg.text}
        </span>
      )}
    </span>
  );
}

export function AdminScreen() {
  const navigate = useNavigate();
  const me = usePolling<any>(() => api.get(endpoints.me), 60000);
  const cc = usePolling<any>(() => api.get(endpoints.adminCommandCenter), 20000);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const usersPoll = usePolling<{ users: AdminUser[] }>(
    () => api.get(endpoints.adminUsers), 20000, [reload]);

  useEffect(() => { setReload((r) => r + 1); }, [cc.data?.users?.total]);

  const users: AdminUser[] = usersPoll.data?.users ?? [];
  const isAdmin = me.data?.role === "admin";

  const runUserAction = useCallback(async (uid: string, fn: () => Promise<any>, okMsg: string) => {
    setBusyId(uid);
    try { await fn(); setReload((r) => r + 1); return okMsg; }
    finally { setBusyId(null); }
  }, []);

  if (me.loading && !me.data) return <Spinner label="Checking access..." />;
  if (!isAdmin) {
    return (
      <div className="animate-fadeUp">
        <Glass className="flex flex-col items-center gap-3 p-10 text-center">
          <ShieldAlert size={34} className="text-amber-300" />
          <p className="text-[15px] font-semibold">Admin only</p>
          <p className="max-w-[320px] text-[12.5px] text-txt-mid">
            This console is restricted to developer/admin accounts
            (backend-enforced - the API checks the role on every request).
          </p>
        </Glass>
      </div>
    );
  }

  const d = cc.data ?? {};
  const health = d.health?.components ?? {};
  const walls = d.daily_walls ?? {};
  const tpa = d.tp_audit ?? {};
  const learn = d.learning ?? {};
  const regimes = d.regimes ?? {};

  return (
    <div className="animate-fadeUp space-y-6">
      <PageHeader title="Command Center" sub="Developer console - every value from backend truth"
        icon={<Shield size={20} />}
        right={
          <button onClick={() => { cc.refresh(); usersPoll.refresh(); }}
            className="tap flex items-center gap-1.5 rounded-xl border border-[rgba(var(--p-rgb),0.25)] px-3 py-1.5 text-[11.5px] text-txt-mid hover:bg-[rgba(var(--p-rgb),0.08)]">
            <RefreshCw size={13} className={cc.loading ? "animate-spin" : ""} /> Refresh
          </button>
        } />

      {/* --- system snapshot --- */}
      <section>
        <SectionHeader>System</SectionHeader>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Glass className="p-4">
            <p className="text-[10.5px] uppercase tracking-wide text-txt-mid">Store</p>
            <p className="mt-1 text-[14px] font-semibold">{health.store?.kind ?? "—"}</p>
            <p className="text-[10.5px] text-txt-mid">execution: {health.execution?.mode ?? "—"}
              {health.execution?.bridge_configured ? " · bridge configured" : ""}</p>
          </Glass>
          <Glass className="p-4">
            <p className="text-[10.5px] uppercase tracking-wide text-txt-mid">Brain 2.0</p>
            <p className="mt-1 text-[14px] font-semibold">{health.brain_v2 ? "ON" : "OFF"}</p>
            <p className="text-[10.5px] text-txt-mid">
              primary ok {num(d.ai_router?.stats?.primary_ok)} · esc ok {num(d.ai_router?.stats?.escalation_ok)}
            </p>
          </Glass>
          <Glass className="p-4">
            <p className="text-[10.5px] uppercase tracking-wide text-txt-mid">Today</p>
            <p className={`mt-1 text-[14px] font-semibold ${num(walls.total_usd) >= 0 ? "text-emerald-300" : "text-red-300"}`}>
              {num(walls.total_usd).toFixed(2)} USD
            </p>
            <p className="text-[10.5px] text-txt-mid">{walls.realized_basis ?? "basis unavailable"}</p>
          </Glass>
          <Glass className="p-4">
            <p className="text-[10.5px] uppercase tracking-wide text-txt-mid">TP audit</p>
            <p className={`mt-1 text-[14px] font-semibold ${num(tpa.leaks) > 0 ? "text-red-300" : "text-emerald-300"}`}>
              {tpa.leaks === undefined ? "—" : num(tpa.leaks)} leak(s)
            </p>
            <p className="text-[10.5px] text-txt-mid">{(tpa.findings ?? []).length} findings</p>
          </Glass>
        </div>
      </section>

      {/* --- users --- */}
      <section>
        <SectionHeader right={<Pill tone={usersPoll.loading ? "neutral" : "cyan"}>{users.length} users</Pill>}>
          Users & auto-trading permissions
        </SectionHeader>
        {usersPoll.loading && !users.length ? <Spinner label="Loading users..." /> :
          users.length === 0 ? <Empty title="No users yet." /> : (
          <div className="space-y-3">
            {users.map((u) => (
              <Glass key={u.id} className="p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 text-[13.5px] font-semibold">
                      {u.email ?? u.id}
                      {u.role === "admin" && <Pill tone="violet">admin</Pill>}
                      {u.status === "suspended" && <Pill tone="neg">suspended</Pill>}
                      {u.trading_permission === "enabled" && <Pill tone="pos">auto ON</Pill>}
                      {u.trading_permission === "setup" && <Pill tone="warn">setup</Pill>}
                      {u.telegram_linked && <Pill tone="cyan">TG</Pill>}
                    </p>
                    <p className="text-[11px] text-txt-mid">
                      permission: {u.trading_permission} · status: {u.status}
                      {u.createdAt ? ` · joined ${shortAgo(u.createdAt)}` : ""}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-start gap-2">
                    {PERMISSIONS.filter((p) => p !== u.trading_permission).map((p) => (
                      <ActionBtn key={p} label={p === "enabled" ? "Enable auto" : `Set ${p}`}
                        busy={busyId === u.id}
                        onRun={() => runUserAction(u.id,
                          () => api.post(endpoints.adminTrading(u.id), { permission: p, reason: "command center" }),
                          `Permission set to ${p}`)} />
                    ))}
                    {u.status !== "suspended" ? (
                      <ActionBtn label="Suspend" tone="danger" busy={busyId === u.id}
                        onRun={() => runUserAction(u.id,
                          () => api.post(endpoints.adminStatus(u.id), { status: "suspended", reason: "command center" }),
                          "Suspended (auto-trading revoked)")} />
                    ) : (
                      <ActionBtn label="Restore" tone="pos" busy={busyId === u.id}
                        onRun={() => runUserAction(u.id,
                          () => api.post(endpoints.adminStatus(u.id), { status: "active", reason: "command center" }),
                          "Restored")} />
                    )}
                    {u.status !== "suspended" && (
                      <ActionBtn label={<span className="inline-flex items-center gap-1"><OctagonX size={12} /> E-stop</span>}
                        tone="danger" busy={busyId === u.id}
                        onRun={() => runUserAction(u.id,
                          () => api.post(endpoints.adminEStop(u.id)),
                          "EMERGENCY STOP executed")} />
                    )}
                  </div>
                </div>
              </Glass>
            ))}
          </div>
        )}
      </section>

      {/* --- learning + regimes --- */}
      <section className="grid gap-3 lg:grid-cols-2">
        <Glass className="p-4">
          <SectionHeader>Learning</SectionHeader>
          <ul className="space-y-1.5 text-[12.5px] text-txt-mid">
            <li>Hypotheses pending: <b className="text-txt-hi">{num(learn.hypotheses_pending)}</b></li>
            <li>Experiments ready for review: <b className="text-txt-hi">{num(learn.experiments_ready_for_review)}</b></li>
            <li>Open recommendations: <b className="text-txt-hi">{num(learn.recommendations_open)}</b></li>
          </ul>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {Object.entries(regimes).map(([m, r]: [string, any]) => (
              <Pill key={m} tone={r?.regime === "TRENDING_BULLISH" ? "pos" : r?.regime === "TRENDING_BEARISH" ? "neg" : "neutral"}>
                {m}: {(r?.regime ?? "?").replace("_", " ").toLowerCase()}
              </Pill>
            ))}
          </div>
        </Glass>
        <Glass className="p-4">
          <SectionHeader>Audit trail (latest)</SectionHeader>
          {(d.audit_tail?.entries ?? []).length === 0 ? <Empty title="No admin actions recorded yet." /> : (
            <ul className="space-y-1.5 text-[11.5px]">
              {(d.audit_tail?.entries ?? []).slice(0, 8).map((a: any) => (
                <li key={a.id} className="flex items-center justify-between gap-2">
                  <span className="truncate text-txt-mid">
                    <StatusDot tone={String(a.action).includes("emergency") ? "red" : "green"} /> {a.action} → {a.target_user ?? "—"}
                  </span>
                  <span className="shrink-0 text-[10px] text-txt-mid">{shortAgo(a.at ?? a.createdAt)}</span>
                </li>
              ))}
            </ul>
          )}
        </Glass>
      </section>

      {/* --- execution ledger --- */}
      <section>
        <SectionHeader right={<Pill tone="cyan">live</Pill>}>Execution lifecycle (latest)</SectionHeader>
        <Glass className="divide-y divide-[rgba(var(--warm-rgb),0.06)]">
          {(d.executions?.recent ?? []).length === 0 ? (
            <div className="p-4"><Empty title="No execution events yet." /></div>
          ) : (d.executions?.recent ?? []).map((e: any) => (
            <div key={e.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-[12px]">
              <span className="font-medium">{e.kind}</span>
              <span className="text-txt-mid">{e.detail || "—"}</span>
              <Pill tone={e.stage === "CONFIRMED" ? "pos" : e.stage === "FAILED" ? "neg" : "neutral"}>{e.stage}</Pill>
              <span className="text-[10px] text-txt-mid">{shortAgo(e.createdAt)}</span>
            </div>
          ))}
        </Glass>
      </section>
    </div>
  );
}
