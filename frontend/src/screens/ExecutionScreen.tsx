import { RefreshCw, ListChecks } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import { TcaPanel } from "../components/intel30";
import { Empty, ErrorNote, Glass, PageHeader, Pill, SectionHeader, Spinner } from "../components/ui";
import { shortAgo } from "../lib/format";

type ExecEvent = {
  id: string; kind: string; stage: string; market?: string | null;
  signal_id?: string | null; ticket?: number | null; detail?: string;
  latency_ms?: number | null; createdAt: string;
};
type Finding = { type: string; severity: string; detail: string; ticket?: number | null };

const stageTone = (s: string) =>
  s === "CONFIRMED" ? "pos" : s === "FAILED" ? "neg" :
  s === "SKIPPED" || s === "EXPIRED" ? "warn" : "neutral";

/** P15 IA - Execution: the append-only lifecycle ledger + TP-leak audit.
 *  Everything the system CLAIMED about orders, reconcilable with the broker. */
export function ExecutionScreen() {
  const ledger = usePolling<{ events: ExecEvent[] }>(
    () => api.get(endpoints.executions), 8000);
  const audit = usePolling<{ findings: Finding[]; leaks: number; scanned_at?: string }>(
    () => api.get(endpoints.tpAudit), 30000);

  return (
    <div className="animate-fadeUp space-y-6">
      <PageHeader title="Execution" sub="Order lifecycle ledger + TP-leak audit"
        icon={<ListChecks size={20} />}
        right={
          <button onClick={() => { ledger.refresh(); audit.refresh(); }}
            className="tap flex items-center gap-1.5 rounded-xl border border-[rgba(var(--p-rgb),0.25)] px-3 py-1.5 text-[11.5px] text-txt-mid hover:bg-[rgba(var(--p-rgb),0.08)]">
            <RefreshCw size={13} className={ledger.loading ? "animate-spin" : ""} /> Refresh
          </button>
        } />
      <ErrorNote message={ledger.error ?? audit.error} />

      {/* TP audit */}
      <section>
        <SectionHeader right={<Pill tone={audit.data?.leaks ? "neg" : "pos"}>
          {audit.data ? `${audit.data.leaks} leak(s)` : "…"}
        </Pill>}>TP-leak audit (claims vs broker)</SectionHeader>
        {audit.loading && !audit.data ? <Spinner label="Auditing..." /> :
          (audit.data?.findings ?? []).length === 0 ? (
            <Glass className="p-5"><Empty title="Clean - no findings"
              sub="TP locks held, no orphan positions, journal in sync." icon={<ListChecks size={24} />} /></Glass>
          ) : (
            <div className="space-y-2">
              {audit.data!.findings.map((f, i) => (
                <Glass key={i} className="flex items-center justify-between gap-3 px-4 py-3">
                  <span className="min-w-0 text-[12px] text-txt-mid">{f.detail}</span>
                  <Pill tone={f.severity === "leak" ? "neg" : f.severity === "warn" ? "warn" : "neutral"}>
                    {f.type}
                  </Pill>
                </Glass>
              ))}
            </div>
          )}
      </section>

      {/* ledger */}
      <section>
        <SectionHeader right={<Pill tone="cyan">{ledger.data?.events?.length ?? 0} events</Pill>}>
          Lifecycle ledger (append-only)
        </SectionHeader>
        {ledger.loading && !ledger.data ? <Spinner label="Loading ledger..." /> :
          (ledger.data?.events ?? []).length === 0 ? (
            <Glass className="p-5"><Empty title="No execution events yet"
              sub="Every entry, SL move, partial and close will appear here with its stage." /></Glass>
          ) : (
            <Glass className="divide-y divide-[rgba(var(--warm-rgb),0.06)]">
              {ledger.data!.events.map((e) => (
                <div key={e.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-[12px]">
                  <span className="w-[110px] font-semibold">{e.kind}</span>
                  <span className="min-w-0 flex-1 truncate text-txt-mid">
                    {e.detail || "—"}{e.market ? ` · ${e.market}` : ""}
                    {e.ticket ? ` · #${e.ticket}` : ""}
                  </span>
                  {e.latency_ms != null && <span className="text-[10px] text-txt-mid">{e.latency_ms} ms</span>}
                  <Pill tone={stageTone(e.stage)}>{e.stage}</Pill>
                  <span className="w-[54px] text-right text-[10px] text-txt-mid">{shortAgo(e.createdAt)}</span>
                </div>
              ))}
            </Glass>
          )}
      </section>

      <section>
        <TcaPanel />
      </section>
    </div>
  );
}
