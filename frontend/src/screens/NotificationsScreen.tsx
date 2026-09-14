import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { api, endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";
import type { Notification } from "../lib/types";
import { Divider, Empty, Glass, GlowDot, Pill, Spinner } from "../components/ui";
import { shortAgo } from "../lib/format";

const toneFor: Record<string, "pos" | "neg" | "warn" | "acc" | "violet" | "cyan"> = {
  NEW_SIGNAL: "cyan",
  TP1_HIT: "pos",
  TP2_HIT: "pos",
  TP3_HIT: "pos",
  SL_HIT: "neg",
  TRADE_COMPLETED: "acc",
  NEW_LESSON: "violet",
  APPROVAL_REQUIRED: "warn",
  STRATEGY_VERSION_UPDATED: "pos",
  SIGNAL_SKIPPED: "acc",
};

export function NotificationsScreen() {
  const navigate = useNavigate();
  const { data, loading } = usePolling<{ notifications: Notification[]; unread: number }>(() => api.get(endpoints.notifications), 6000);

  useEffect(() => {
    if (data && data.unread > 0) api.post(endpoints.notificationsRead, {}).catch(() => {});
  }, [data?.unread]);

  if (loading && !data) return <Spinner label="Loading notifications..." />;

  return (
    <div className="animate-fadeUp">
      <header className="mb-7 flex items-center gap-2">
        <button onClick={() => navigate(-1)} className="tap -ml-2 flex h-9 w-9 items-center justify-center rounded-full text-txt-mid transition hover:bg-white/[0.04] hover:text-txt-hi">
          <ArrowLeft size={17} />
        </button>
        <h1 className="text-[22px] font-semibold tracking-tight">Notifications</h1>
      </header>

      {(data?.notifications ?? []).length === 0 ? (
        <Empty title="Nothing yet" sub="New signals, TP/SL hits, lessons and approval requests will land here." />
      ) : (
        <Glass pad={false} className="divide-y divide-white/[0.05] !p-0">
          {data!.notifications.map((n) => (
            <div key={n.id} className={`px-5 py-4 ${!n.read ? "" : "opacity-60"}`}>
              <div className="flex items-start gap-3.5">
                <span className="mt-[5px]">
                  <GlowDot tone={toneFor[n.type] ?? "acc"} size={n.read ? 5 : 7} pulse={!n.read && n.type === "APPROVAL_REQUIRED"} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-3">
                    <span className={`text-[13px] ${n.read ? "font-normal text-txt-mid" : "font-medium text-txt-hi"}`}>{n.title}</span>
                    <span className="shrink-0 text-[9px] text-txt-faint">{shortAgo(n.createdAt)}</span>
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-[11.5px] leading-relaxed text-txt-low">{n.body}</p>
                  <div className="mt-2">
                    <Pill tone={toneFor[n.type] ?? "neutral"}>{n.type.replace(/_/g, " ").toLowerCase()}</Pill>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </Glass>
      )}
    </div>
  );
}
