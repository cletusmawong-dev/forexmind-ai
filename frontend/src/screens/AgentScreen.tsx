import { useState } from "react";
import { LineChart, Send } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { ActivityItem, AgentStatus } from "../lib/types";
import { AnimatedNumber, DemoTag, Divider, Glass, PageHeader, Pill, ProgressBar, Segmented, SectionHeader, Spinner, StatusDot } from "../components/ui";
import { fmtPct, fmtTime, shortAgo } from "../lib/format";
import { AgentPulse } from "../components/AgentPulse";

const QUICK = [
  "What have you learned from Strategy 2?",
  "Show me pending suggestions",
  "Why did the last trade lose?",
  "Which strategy performs better?",
  "Show me the last five experiments",
  "What is your objective?",
];

interface ChatMsg {
  role: "user" | "agent";
  text: string;
}

export function AgentScreen() {
  const [tab, setTab] = useState<"presence" | "timeline" | "chat">("presence");
  const status = usePolling<AgentStatus>(() => api.get(endpoints.agentStatus), 4000);
  const activity = usePolling<{ activity: ActivityItem[] }>(() => api.get(endpoints.agentActivity), 4000);

  const [messages, setMessages] = useState<ChatMsg[]>([
    {
      role: "agent",
      text: "I'm your ForexMind research agent. I answer only from stored data — I never invent results. Ask me why a signal qualified, what I've learned, or what I'm suggesting.",
    },
  ]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);

  const send = async (text?: string) => {
    const msg = (text ?? input).trim();
    if (!msg || thinking) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: msg }]);
    setThinking(true);
    try {
      const res = await api.post<{ reply: string }>(endpoints.chat, { message: msg });
      setMessages((m) => [...m, { role: "agent", text: res.reply }]);
    } catch (e: any) {
      setMessages((m) => [...m, { role: "agent", text: `Sorry — ${e.message || "I could not process that."}` }]);
    } finally {
      setThinking(false);
    }
  };

  if (!status.data) {
    if (status.loading) return <Spinner label="Waking your agent…" />;
    return <div className="pt-10"><Spinner label="…" /></div>;
  }
  const st = status.data;
  const p = st.progress;

  const line =
    st.current_task === "Tracking active signals"
      ? "I'm following active signals to their outcomes."
      : st.current_task?.startsWith("Scanning")
      ? "I'm monitoring the markets for high-quality setups."
      : "I'm monitoring the markets for high-quality setups.";

  return (
    <div className="anim-fadeUp">
      <PageHeader title="Agent" right={<DemoTag />} />

      <Segmented
        className="mb-7"
        value={tab}
        onChange={(k) => setTab(k as any)}
        options={[
          { key: "presence", label: "Overview" },
          { key: "timeline", label: "Timeline" },
          { key: "chat", label: "Chat" },
        ]}
      />

      {tab === "presence" && (
        <div className="lg:grid lg:grid-cols-12 lg:gap-10">
          <div className="lg:col-span-7">
            {/* ---- AI status card ---- */}
            <Glass className="relative overflow-hidden" pad={false}>
              <div
                className="pointer-events-none absolute -right-16 -top-24 h-60 w-60 rounded-full opacity-25 blur-3xl anim-drift"
                style={{ background: "conic-gradient(from 180deg, #3e7bfa, #8e7bff, #33d6f6, #3e7bfa)" }}
              />
              <div className="relative p-6">
                <div className="flex items-start gap-4">
                  <div
                    className="flex h-[76px] w-[76px] shrink-0 items-center justify-center rounded-full"
                    style={{
                      border: "1.5px solid rgba(47,217,138,0.35)",
                      background: "radial-gradient(circle at 32% 28%, rgba(31,90,70,0.55), rgba(8,14,30,0.95) 74%)",
                      boxShadow: "0 0 30px rgba(47,217,138,0.22), inset 0 1px 0 rgba(255,255,255,0.1)",
                    }}
                  >
                    <LineChart size={26} style={{ color: "#3ee6a0", filter: "drop-shadow(0 0 8px rgba(47,217,138,0.8))" }} strokeWidth={2.1} />
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-[12px] text-[var(--text-secondary)]">
                      <StatusDot tone="green" /> Active
                    </div>
                    <p className="mt-2 text-[15px] font-medium leading-snug">"{line}"</p>
                    <p className="mt-1.5 text-[11px] text-[var(--text-muted)]">
                      {st.ai.provider === "xkiro" ? "XKiro reasoning · server-side" : "Grounded analyst · no external AI configured"}
                    </p>
                  </div>
                </div>
              </div>
              <Divider />
              <div className="grid grid-cols-3 divide-x divide-white/[0.06]">
                <MiniStat label="Signals today" value={st.signals_today} />
                <MiniStat label="Lessons" value={st.lessons} tone="text-[#b3a6ff]" />
                <MiniStat label="Approvals" value={st.pending_approvals} tone={st.pending_approvals > 0 ? "text-[var(--accent-amber)]" : ""} />
              </div>
            </Glass>

            {/* ---- agent pulse: live activity proof ---- */}
            <div className="mt-4">
              <AgentPulse />
            </div>

            {/* ---- objective ---- */}
            <SectionHeader className="mt-8">Current objective</SectionHeader>
            <Glass>
              <div className="flex items-end justify-between">
                <AnimatedNumber value={p.daily_pl_pct} format={(v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`} className="num text-[36px] font-extrabold leading-none tracking-[-0.03em]" />
                <span className="num pb-1 text-[12px] text-[var(--text-secondary)]">
                  <span className="font-semibold">{fmtPct(p.daily_pl_pct)}</span> / +{p.objective_pct}%
                </span>
              </div>
              <div className="mt-4">
                <ProgressBar pct={Math.max(0, (p.daily_pl_pct / Math.max(p.objective_pct, 0.01)) * 100)} tone={p.daily_pl_pct >= 0 ? "green" : "blue"} />
              </div>
              <p className="mt-3 text-[10.5px] text-[var(--text-muted)]">
                Weekly {fmtPct(p.weekly_pl_pct)} · an objective guides my research — it never forces signals.
              </p>
            </Glass>

            {/* ---- strategy versions ---- */}
            <SectionHeader className="mt-8">Strategy versions</SectionHeader>
            <div className="space-y-3">
              {Object.entries(st.strategy_versions).map(([sid, v], i) => (
                <a key={sid} href="/strategies" className="glass glass-hover tap flex items-center justify-between p-4">
                  <div>
                    <div className="text-[13px] font-semibold">
                      Strategy {i + 1} · {sid === "strategy_1_zero_lag" ? "Zero Lag Trend" : "9/21 EMA Smart TP/SL"}
                    </div>
                    <div className="mt-0.5 text-[10.5px] text-[var(--text-muted)]">version {v}</div>
                  </div>
                  <Pill tone="blue">v{v}</Pill>
                </a>
              ))}
            </div>
          </div>

          <div className="mt-10 lg:col-span-5 lg:mt-0 lg:border-l lg:border-white/[0.06] lg:pl-10">
            <SectionHeader>Live activity</SectionHeader>
            <Glass pad={false} className="!p-5">
              <Timeline items={activity.data?.activity ?? []} />
            </Glass>
            <button
              className="btn-ghost mt-4 w-full"
              onClick={async () => {
                await api.post(endpoints.agentScan);
                activity.refresh();
              }}
            >
              Run observation pass
            </button>
            <div className="mt-4 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4 text-[10.5px] leading-relaxed text-[var(--text-muted)]">
              Market data:{" "}
              {st.market_data?.demo === false ? (
                <>
                  <span className="font-semibold text-[var(--accent-green)]">LIVE · REAL-TIME feed</span> — signals from live prices.
                </>
              ) : (
                <>
                  <span className="font-semibold text-[var(--accent-amber)]">DEMO · HISTORICAL replay</span> — never presented as live.
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {tab === "timeline" && (
        <div className="lg:mx-auto lg:max-w-2xl">
          <SectionHeader>Full activity log</SectionHeader>
          <Glass pad={false} className="!p-6">
            <Timeline items={(activity.data?.activity ?? []).slice(0, 60)} />
          </Glass>
        </div>
      )}

      {tab === "chat" && (
        <div className="lg:mx-auto lg:max-w-2xl">
          <Glass className="flex min-h-[480px] flex-col !p-4" pad={false}>
            <div className="flex-1 space-y-4 overflow-y-auto p-2 pb-3">
              {messages.map((m, i) => (
                <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"} anim-fadeUp`}>
                  {m.role === "agent" && (
                    <div
                      className="mr-2.5 mt-1.5 h-6 w-6 shrink-0 rounded-full border border-[rgba(77,124,254,0.4)]"
                      style={{ background: "radial-gradient(circle at 30% 30%, rgba(62,123,250,0.7), rgba(12,18,38,0.95))", boxShadow: "0 0 14px rgba(62,124,250,0.4)" }}
                    />
                  )}
                  <div
                    className={`max-w-[82%] whitespace-pre-wrap rounded-[20px] px-4 py-3 text-[12.5px] leading-relaxed ${
                      m.role === "user" ? "rounded-br-md text-white" : "rounded-bl-md border border-white/[0.08] bg-white/[0.04] text-[var(--text-secondary)]"
                    }`}
                    style={m.role === "user" ? { background: "linear-gradient(120deg,#3e7bfa,#2bb8ec)", boxShadow: "0 8px 24px rgba(62,124,250,0.3)" } : undefined}
                  >
                    {m.text}
                  </div>
                </div>
              ))}
              {thinking && (
                <div className="flex justify-start">
                  <div className="ml-[34px] flex gap-1.5 rounded-[20px] rounded-bl-md border border-white/[0.08] bg-white/[0.04] px-4 py-3.5">
                    {[0, 1, 2].map((i) => (
                      <span key={i} className="h-1.5 w-1.5 rounded-full bg-[var(--accent-blue)] anim-pulse" style={{ animationDelay: `${i * 0.35}s` }} />
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div className="no-scrollbar mb-3 flex gap-2 overflow-x-auto px-1">
              {QUICK.map((q) => (
                <button key={q} onClick={() => send(q)} className="shrink-0 rounded-full border border-white/[0.08] bg-white/[0.03] px-3.5 py-2 text-[10.5px] text-[var(--text-muted)] transition hover:text-[var(--text-secondary)]">
                  {q}
                </button>
              ))}
            </div>
            <div className="flex gap-2.5 px-1">
              <input className="input" placeholder="Ask your agent…" value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} aria-label="Message the agent" />
              <button className="btn-primary !px-4" onClick={() => send()} disabled={thinking} aria-label="Send">
                <Send size={15} />
              </button>
            </div>
          </Glass>
        </div>
      )}
    </div>
  );
}

function MiniStat({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return (
    <div className="px-4 py-4 text-center">
      <div className={`num text-[19px] font-semibold ${tone || "text-[var(--text-primary)]"}`}>{value}</div>
      <div className="mt-1 text-[8.5px] font-bold uppercase tracking-[0.14em] text-[var(--text-muted)]">{label}</div>
    </div>
  );
}

function Timeline({ items }: { items: ActivityItem[] }) {
  return (
    <div className="relative space-y-4">
      <span className="absolute bottom-1 left-[4.5px] top-1 w-px bg-gradient-to-b from-[rgba(77,124,254,0.4)] via-[rgba(77,124,254,0.12)] to-transparent" />
      {items.slice(0, 40).map((a, i) => (
        <div key={a.id} className="relative flex items-start gap-4 anim-fadeUp" style={{ animationDelay: `${Math.min(i * 45, 400)}ms` }}>
          <span className="relative mt-[5px] flex h-[10px] w-[10px] shrink-0">
            {i === 0 && <span className="absolute inset-0 rounded-full bg-[var(--accent-blue)] opacity-40 anim-pulse" style={{ transform: "scale(2.1)" }} />}
            <span className={`relative h-[10px] w-[10px] rounded-full ${i === 0 ? "bg-[var(--accent-blue)]" : "bg-white/20"}`} style={i === 0 ? { boxShadow: "0 0 12px rgba(77,124,254,0.95)" } : {}} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[12px] leading-snug text-[var(--text-secondary)]">{a.message}</div>
            <div className="mt-0.5 flex items-center gap-2 font-mono text-[9px] text-[var(--text-muted)]">
              {fmtTime(a.ts_override || a.createdAt)}
              {i === 0 && <span className="font-sans">{shortAgo(a.ts_override || a.createdAt)}</span>}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
