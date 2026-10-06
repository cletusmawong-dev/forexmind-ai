import { useEffect, useRef, useState } from "react";
import { ChevronDown, Plus, ShieldCheck } from "lucide-react";
import { api } from "../lib/api";
import { usePolling } from "../lib/usePolling";

type Account = {
  id: string; label: string; mode: string; is_active: boolean;
  is_default: boolean; trading_enabled: boolean; connected: boolean;
};

/** Final build section 43: the active trading account is ALWAYS visible and
 *  switchable. Switching re-loads the whole app context (dashboard, signals,
 *  positions, risk) - the backend stamps every execution with the selected
 *  account, so the UI can never show one account while another executes. */
export function AccountSwitcher() {
  const { data, refresh } = usePolling<{ accounts: Account[] }>(
    () => api.get("/api/accounts"), 30000);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [label, setLabel] = useState("");
  const [mode, setMode] = useState("own_pc");
  const box = useRef<HTMLDivElement>(null);

  const accounts = data?.accounts ?? [];
  const active = accounts.find((a) => a.is_active);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const select = async (id: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await api.post(`/api/accounts/${id}/select`);
      window.location.reload();   // full context switch (section 43)
    } finally {
      setBusy(false);
    }
  };

  const add = async () => {
    if (!label.trim() || busy) return;
    setBusy(true);
    try {
      await api.post("/api/accounts", { label: label.trim(), mode });
      setLabel(""); setAdding(false); setOpen(true); refresh();
    } catch { /* surfaced by the request layer */ }
    setBusy(false);
  };

  return (
    <div ref={box} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label="Switch trading account"
        className="flex max-w-full items-center gap-1.5 rounded-xl border border-[rgba(var(--warm-rgb),0.12)] bg-[rgba(var(--warm-rgb),0.05)] px-3 py-1.5 text-left">
        <ShieldCheck size={13} className={`shrink-0 ${active?.trading_enabled ? "text-pos" : "text-txt-faint"}`} />
        <span className="min-w-0">
          <span className="block text-[9px] uppercase tracking-[0.14em] text-txt-faint">
            {active ? `Account` : "No account"}
          </span>
          <span className="block truncate text-[11.5px] font-semibold text-txt-hi">
            {active ? active.label : "Signals only"}
          </span>
        </span>
        <ChevronDown size={13} className={`shrink-0 text-txt-faint transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="glass-float absolute right-0 top-[calc(100%+8px)] z-50 w-64 rounded-2xl p-2 animate-fadeUp">
          {accounts.map((a) => (
            <button key={a.id} disabled={busy} onClick={() => select(a.id)}
                    className="flex w-full items-center justify-between gap-2 rounded-xl px-3 py-2.5 text-left hover:bg-[rgba(var(--p-rgb),0.06)]">
              <span className="min-w-0">
                <span className="block truncate text-[12px] font-semibold text-txt-hi">{a.label}</span>
                <span className="block text-[9.5px] text-txt-faint">
                  {a.mode.replace("_", " ")} · {a.trading_enabled ? "trading ON" : "trading OFF"}
                </span>
              </span>
              {a.is_active && (
                <span className="shrink-0 rounded-full bg-[rgba(var(--pos-rgb),0.15)] px-2 py-0.5 text-[8.5px] font-bold uppercase tracking-wide text-pos">Active</span>
              )}
            </button>
          ))}
          {!adding ? (
            <button onClick={() => setAdding(true)} disabled={busy}
                    className="mt-1 flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left text-[12px] font-semibold text-acc-cyan hover:bg-[rgba(var(--p-rgb),0.06)]">
              <Plus size={14} /> Add trading account
            </button>
          ) : (
            <div className="mt-1 rounded-xl border border-[rgba(var(--warm-rgb),0.12)] p-2.5">
              <input value={label} onChange={(e) => setLabel(e.target.value)}
                     placeholder="e.g. Exness Live"
                     className="input mb-2 !px-2.5 !py-2 text-[12px]" />
              <select value={mode} onChange={(e) => setMode(e.target.value)}
                      className="input mb-2 !px-2.5 !py-2 text-[12px]">
                <option value="own_pc">My PC connector (MT5 on this PC)</option>
                <option value="signals_only">Signals only</option>
              </select>
              <div className="flex gap-2">
                <button onClick={add} disabled={busy || !label.trim()}
                        className="btn-ghost flex-1 !py-1.5 text-[11px]">{busy ? "Adding..." : "Add"}</button>
                <button onClick={() => setAdding(false)}
                        className="btn-ghost flex-1 !py-1.5 text-[11px]">Cancel</button>
              </div>
              <p className="mt-1.5 text-[9px] leading-relaxed text-txt-faint">
                New accounts start signals-only and trading OFF.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
