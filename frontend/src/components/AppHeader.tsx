/**
 * AppHeader - compact mobile top bar (approved reference 2026-10-26).
 * Gold FOREXMIND AI lockup left; live account switcher, notifications
 * and profile right. Desktop keeps the Sidebar - this bar is mobile-only.
 * All data comes from real endpoints (AccountSwitcher polls /api/accounts,
 * profile initial from /api/me). No fake badges.
 */
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Bell, UserRound } from "lucide-react";
import { Logo } from "./ui";
import { AccountSwitcher } from "./AccountSwitcher";
import { api } from "../lib/api";
import { endpoints } from "../lib/api";
import { usePolling } from "../lib/usePolling";

function useMeInitial() {
  const [state, setState] = useState<{ initial: string; loaded: boolean }>({ initial: "", loaded: false });
  useEffect(() => {
    let dead = false;
    api
      .get<{ email?: string; name?: string; full_name?: string }>("/api/me")
      .then((me) => {
        if (dead) return;
        const src = me?.name || me?.full_name || me?.email || "";
        const initial = String(src).trim().charAt(0).toUpperCase() || "F";
        setState({ initial, loaded: true });
      })
      .catch(() => { if (!dead) setState({ initial: "F", loaded: true }); });
    return () => { dead = true; };
  }, []);
  return state;
}

export function AppHeader() {
  const location = useLocation();
  const me = useMeInitial();
  const notifs = usePolling<{ unread: number }>(() => api.get(endpoints.notifications), 60000);
  const unread = notifs.data?.unread ?? 0;
  const onNotifications = location.pathname.startsWith("/notifications");
  const onMore = location.pathname.startsWith("/more") || location.pathname.startsWith("/settings");

  return (
    <header
      className="hairline-b sticky top-0 z-[70] bg-[var(--bg-primary)]/95 backdrop-blur-sm lg:hidden"
      style={{ paddingTop: "max(env(safe-area-inset-top, 0px), 10px)" }}
    >
      <div className="mx-auto flex h-[52px] w-full max-w-[560px] items-center justify-between gap-2 px-4 pb-2.5">
        <Link to="/" aria-label="ForexMind AI home" className="flex min-w-0 items-center">
          <Logo size={30} />
        </Link>

        <div className="flex min-w-0 items-center gap-1.5">
          <div className="min-w-0 [&_button]:!py-2 [&_.relative]:max-w-[130px]">
            <AccountSwitcher />
          </div>

          <Link
            to="/notifications"
            aria-label="Notifications"
            className={`tap relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full border ${
              onNotifications ? "border-transparent bg-[rgb(var(--p-rgb))] text-white" : "border-[var(--glass-border)] bg-white text-[var(--text-secondary)]"
            }`}
          >
            <Bell size={16} strokeWidth={onNotifications ? 2.2 : 1.8} />
            {unread > 0 && (
              <span className="absolute -right-1 -top-1 flex h-[15px] min-w-[15px] items-center justify-center rounded-full border-[1.5px] border-white bg-[var(--accent-red)] px-0.5 text-[8px] font-bold text-white">
                {unread > 99 ? "99+" : unread}
              </span>
            )}
          </Link>

          <Link
            to="/more"
            aria-label="Profile and settings"
            className={`tap flex h-9 w-9 shrink-0 items-center justify-center rounded-full border text-[12px] font-bold ${
              onMore
                ? "border-transparent bg-[rgb(var(--p-rgb))] text-white"
                : "border-[var(--glass-border)] bg-white text-[var(--text-secondary)]"
            }`}
          >
            {me.loaded && me.initial !== "F" ? me.initial : <UserRound size={15} strokeWidth={1.9} />}
          </Link>
        </div>
      </div>
    </header>
  );
}
