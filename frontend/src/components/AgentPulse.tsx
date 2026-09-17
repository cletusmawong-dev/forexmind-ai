import { useEffect, useState } from "react";
import { Radio } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { ActivityItem, AgentStatus } from "../lib/types";
import { Divider, Glass } from "./ui";

function ago(ts: number): string {
  const s = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (s < 90) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m ago`;
}

/** Live "is the agent actually working" box: 24h activity histogram +
 *  last-event liveness + today's counters. Fed purely by real activity data. */
export function AgentPulse() {
  const activity = usePolling<{ activity: ActivityItem[] }>(() => api.get(`${endpoints.agentActivity}?limit=200`), 6000);
  const status = usePolling<AgentStatus>(() => api.get(endpoints.agentStatus), 10000);
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 30000);
    return () => clearInterval(id);
  }, []);

  const items = activity.data?.activity ?? [];
  const tsOf = (a: ActivityItem) => Date.parse((a.ts_override || a.createdAt || "") as string) || 0;

  // 24 hourly buckets, oldest -> newest
  const buckets = new Array(24).fill(0);
  let lastTs = 0;
  for (const a of items) {
    const t = tsOf(a);
    if (!t) continue;
    if (t > lastTs) lastTs = t;
    const hAgo = Math.floor((Date.now() - t) / 3_600_000);
    if (hAgo >= 0 && hAgo < 24) buckets[23 - hAgo]++;
  }
  const total24 = buckets.reduce((x, y) => x + y, 0);
  const peak = Math.max(...buckets, 1);
  const live = lastTs > 0 && Date.now() - lastTs < 15 * 60_000;

  const hourLabel = (i: number) => {
    const d = new Date(Date.now() - (23 - i) * 3_600_000);
    return `${String(d.getHours()).padStart(2, "0")}:00 - ${buckets[i]} event${buckets[i] === 1 ? "" : "s"}`;
  };

  return (
    <Glass pad={false} className="!p-5">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-[13.5px] font-bold text-[var(--text-primary)]">
          <Radio size={14} className="text-[var(--accent-cyan)]" /> Agent Pulse
        </span>
        <span
          className="flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[9px] font-bold uppercase tracking-[0.12em]"
          style={{
            borderColor: live ? "rgba(18,155,127,0.35)" : "rgba(217,131,36,0.35)",
            color: live ? "var(--accent-green)" : "var(--accent-amber)",
            background: live ? "rgba(18,155,127,0.08)" : "rgba(217,131,36,0.08)",
          }}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-[var(--accent-green)] anim-pulse" : "bg-[var(--accent-amber)]"}`} />
          {live ? "live" : lastTs ? "idle" : "booting"}
        </span>
      </div>

      {/* histogram */}
      <div className="mt-4 flex h-[72px] items-end gap-[2.5px]" role="img" aria-label="Agent activity, last 24 hours">
        {buckets.map((n, i) => (
          <div key={i} className="group relative flex-1">
            <div
              className="w-full rounded-t-[3px] transition-all duration-500"
              style={{
                height: `${n === 0 ? 3 : Math.max(8, (n / peak) * 68)}px`,
                background:
                  n === 0
                    ? "rgba(184,145,47,0.12)"
                    : i === 23
                    ? "linear-gradient(to top, #0e8f78, #35c1a4)"
                    : "linear-gradient(to top, rgba(184,145,47,0.45), rgba(212,175,55,0.85))",
                boxShadow: n > 0 && i === 23 ? "0 0 12px rgba(18,155,127,0.55)" : undefined,
              }}
              title={hourLabel(i)}
            />
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex justify-between text-[8.5px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted)]">
        <span>24h ago</span>
        <span>12h</span>
        <span>now</span>
      </div>

      <Divider className="my-4" />

      <div className="grid grid-cols-3 divide-x divide-[rgba(122,92,34,0.07)]">
        <PulseStat label="Events - 24h" value={total24} tone="text-[#2c3548]" />
        <PulseStat label="Signals today" value={status.data?.signals_today ?? "-"} />
        <PulseStat label="Lessons" value={status.data?.lessons ?? "-"} tone="text-[#c94a3d]" />
      </div>

      <p className="mt-3 text-[10.5px] text-[var(--text-muted)]">
        Last activity: <span className="font-semibold text-[var(--text-secondary)]">{lastTs ? ago(lastTs) : "waiting for first scan..."}</span>
      </p>
    </Glass>
  );
}

function PulseStat({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return (
    <div className="px-2 text-center">
      <div className={`num text-[19px] font-bold ${tone || "text-[var(--text-primary)]"}`}>{value}</div>
      <div className="mt-0.5 text-[8px] font-bold uppercase tracking-[0.13em] text-[var(--text-muted)]">{label}</div>
    </div>
  );
}
