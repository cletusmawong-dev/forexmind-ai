import { useState } from "react";
import { Send } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { ActivityItem, AgentStatus } from "../lib/types";
import { ConnectionState, DemoTag, Divider, Eyebrow, Glass, GlowDot, Orb, Pill, Segmented, Spinner, ThinProgress, AnimatedNumber } from "../components/ui";
import { fmtPct, fmtTime, shortAgo } from "../lib/format";

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
    return <ConnectionState onRetry={status.refresh} label="Can't reach your agent" />;
  }
  const st = status.data;
  const p = st.progress;
  const scanMatch = st.current_task.match(/Scanning (\w+)/);
  const line = scanMatch
    ? `I'm currently scanning ${scanMatch[1]}.`
    : st.current_task === "Tracking active signals"
    ? "I'm following active signals to their outcomes."
    : "I'm monitoring the markets for high-quality setups.";

  return (
    <div className="animate-fadeUp">
      <header className="mb-7 flex items-start justify-between">
        <h1 className="pt-1 text-[22px] font-semibold tracking-tight">Agent</h1>
        <DemoTag />
      </header>

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
            {/* ---- AI presence ---- */}
            <Glass className="relative overflow-hidden" pad={false}>
              <div
                className="pointer-events-none absolute -right-16 -top-24 h-64 w-64 rounded-full opacity-[0.14] blur-3xl animate-drift"
                style={{ background: "conic-gradient(from 180deg, #6C9EFF, #A79BF7, #7CD5F2, #6C9EFF)" }}
              />
              <div className="relative flex items-center gap-5 p-6">
                <Orb size={84} />
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-[12px] text-txt-low">
                    <GlowDot tone="pos" /> Active
                  </div>
                  <p className="mt-1.5 text-[15px] font-medium leading-snug text-txt-hi">"{line}"</p>
                  <p className="mt-1 text-[11px] text-txt-faint">{st.ai.provider === "xkiro" ? "XKiro reasoning · server-side" : "Grounded analyst · no external AI configured"}</p>
                </div>
              </div>
              <Divider />
              <div className="grid grid-cols-3 divide-x divide-white/[0.05]">
                <MiniStat label="Signals today" value={st.signals_today} />
                <MiniStat label="Lessons" value={st.lessons} tone="text-acc-violet" />
                <MiniStat label="Approvals" value={st.pending_approvals} tone={st.pending_approvals > 0 ? "text-warn" : ""} />
              </div>
            </Glass>

            {/* ---- objective ---- */}
            <div className="mt-8 px-1">
              <div className="eyebrow">Current objective</div>
              <div className="mt-2 flex items-end justify-between">
                <AnimatedNumber value={p.daily_pl_pct} format={(v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`} className="text-[34px] font-light text-txt-hi" />
                <span className="num pb-1.5 text-[12px] text-txt-low">
                  <span className="text-txt-mid">{fmtPct(p.daily_pl_pct)}</span> / +{p.objective_pct}%
                </span>
              </div>
              <div className="mt-3">
                <ThinProgress pct={Math.max(0, (p.daily_pl_pct / Math.max(p.objective_pct, 0.01)) * 100)} tone={p.daily_pl_pct >= 0 ? "pos" : "acc"} />
              </div>
              <p className="mt-2.5 text-[10.5px] text-txt-faint">
                Weekly {fmtPct(p.weekly_pl_pct)} · an objective guides my research — it never forces signals.
              </p>
            </div>

            {/* ---- versions ---- */}
            <Eyebrow className="mb-2 mt-9">Strategy versions</Eyebrow>
            <Glass pad={false} className="divide-y divide-white/[0.05] !p-0">
              {Object.entries(st.strategy_versions).map(([sid, v], i) => (
                <a key={sid} href="/strategies" className="tap flex items-center justify-between px-5 py-4 transition hover:bg-white/[0.02]">
                  <span className="text-[13px] text-txt-mid">
                    <span className="text-txt-faint">Strategy {i + 1} · </span>
                    {sid === "strategy_1_zero_lag" ? "Zero Lag Trend" : "9/21 EMA Smart TP/SL"}
                  </span>
                  <Pill tone="cyan">v{v}</Pill>
                </a>
              ))}
            </Glass>
          </div>

          <div className="mt-10 lg:col-span-5 lg:mt-0 lg:border-l lg:border-white/[0.05] lg:pl-10">
            {/* ---- live timeline ---- */}
            <Eyebrow className="mb-3">Live activity</Eyebrow>
            <Timeline items={activity.data?.activity ?? []} />
            <button
              className="btn-ghost mt-5 w-full"
              onClick={async () => {
                await api.post(endpoints.agentScan);
                activity.refresh();
              }}
            >
              Run observation pass
            </button>
          </div>
        </div>
      )}

      {tab === "timeline" && (
        <div className="lg:mx-auto lg:max-w-2xl">
          <Eyebrow className="mb-3">Full activity log</Eyebrow>
          <Glass pad={false} className="!p-6">
            <Timeline items={(activity.data?.activity ?? []).slice(0, 60)} dense />
          </Glass>
        </div>
      )}

      {tab === "chat" && (
        <div className="lg:mx-auto lg:max-w-2xl">
          <Glass className="flex min-h-[480px] flex-col !p-4" pad={false}>
            <div className="flex-1 space-y-4 overflow-y-auto p-2 pb-3">
              {messages.map((m, i) => (
                <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"} animate-fadeUp`}>
                  {m.role === "agent" && (
                    <div className="mr-2.5 mt-1.5 h-6 w-6 shrink-0 rounded-full border border-white/10 bg-base-2/70 p-[3px] backdrop-blur-xl">
                      <div className="h-full w-full rounded-full" style={{ background: "conic-gradient(from 200deg, #6C9EFF, #7CD5F2, #A79BF7, #6C9EFF)", opacity: 0.85 }} />
                    </div>
                  )}
                  <div
                    className={`max-w-[82%] whitespace-pre-wrap rounded-[20px] px-4 py-3 text-[12.5px] leading-relaxed ${
                      m.role === "user"
                        ? "rounded-br-md text-base"
                        : "rounded-bl-md border border-white/[0.07] bg-white/[0.04] text-txt-mid"
                    }`}
                    style={m.role === "user" ? { background: "linear-gradient(120deg,#6C9EFF,#7CD5F2)" } : undefined}
                  >
                    {m.text}
                  </div>
                </div>
              ))}
              {thinking && (
                <div className="flex justify-start">
                  <div className="ml-[34px] flex gap-1.5 rounded-[20px] rounded-bl-md border border-white/[0.07] bg-white/[0.04] px-4 py-3.5">
                    {[0, 1, 2].map((i) => (
                      <span key={i} className="h-1.5 w-1.5 animate-pulseSoft rounded-full bg-acc-cyan" style={{ animationDelay: `${i * 0.35}s` }} />
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div className="no-scrollbar mb-3 flex gap-2 overflow-x-auto px-1">
              {QUICK.map((q) => (
                <button key={q} onClick={() => send(q)} className="shrink-0 rounded-full border border-white/[0.07] bg-white/[0.03] px-3.5 py-1.5 text-[10.5px] text-txt-low transition hover:text-txt-mid">
                  {q}
                </button>
              ))}
            </div>
            <div className="flex gap-2.5 px-1">
              <input className="input" placeholder="Ask your agent…" value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
              <button className="btn-primary !px-4" onClick={() => send()} disabled={thinking}>
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
    <div className="px-5 py-4 text-center">
      <div className={`num text-[18px] font-medium ${tone || "text-txt-hi"}`}>{value}</div>
      <div className="mt-0.5 text-[9.5px] font-medium uppercase tracking-[0.12em] text-txt-faint">{label}</div>
    </div>
  );
}

function Timeline({ items, dense }: { items: ActivityItem[]; dense?: boolean }) {
  return (
    <div className={`relative ${dense ? "space-y-[10px]" : "space-y-[15px]"}`}>
      <span className="absolute bottom-1 left-[3.5px] top-1 w-px bg-gradient-to-b from-white/[0.1] via-white/[0.05] to-transparent" />
      {items.slice(0, dense ? 60 : 10).map((a, i) => (
        <div key={a.id} className="relative flex items-start gap-4 animate-fadeUp" style={{ animationDelay: `${Math.min(i * 50, 400)}ms` }}>
          <span className="relative mt-[5px] flex h-[8px] w-[8px] shrink-0">
            {i === 0 && <span className="absolute inset-0 rounded-full bg-acc-cyan opacity-40 animate-pulseSoft" style={{ transform: "scale(2.1)" }} />}
            <span className={`relative h-[8px] w-[8px] rounded-full ${i === 0 ? "bg-acc-cyan" : "bg-white/20"}`} style={i === 0 ? { boxShadow: "0 0 12px rgba(124,213,242,0.9)" } : {}} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[12px] leading-snug text-txt-mid">{a.message}</div>
            <div className="mt-0.5 font-mono text-[9px] text-txt-faint">{fmtTime(a.ts_override || a.createdAt)}</div>
          </div>
          {!dense && <span className="shrink-0 text-[9.5px] text-txt-faint">{shortAgo(a.ts_override || a.createdAt)}</span>}
        </div>
      ))}
    </div>
  );
}
