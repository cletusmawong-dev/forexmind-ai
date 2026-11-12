import { Link } from "react-router-dom";
import { BarChart3, Bell, BookOpen, Bot, ChevronRight, FlaskConical, Layers, LogOut, Newspaper, Settings as SettingsIcon, ShieldCheck, Users, Zap, Network } from "lucide-react";

const GROUPS: { title: string; items: { to: string; label: string; icon: typeof Bot }[] }[] = [
  {
    title: "Research",
    items: [
      { to: "/agent", label: "AI manager", icon: Bot },
      { to: "/learning", label: "Learning lab", icon: FlaskConical },
      { to: "/pipeline", label: "Research pipeline", icon: Network },
      { to: "/strategies", label: "Strategies", icon: Layers },
      { to: "/analytics", label: "Analytics", icon: BarChart3 },
    ],
  },
  {
    title: "Account",
    items: [
      { to: "/journal", label: "Journal", icon: BookOpen },
      { to: "/execution", label: "Execution", icon: Zap },
      { to: "/news", label: "News", icon: Newspaper },
      { to: "/community", label: "Community", icon: Users },
      { to: "/notifications", label: "Notifications", icon: Bell },
      { to: "/settings", label: "Settings", icon: SettingsIcon },
      { to: "/admin", label: "Admin", icon: ShieldCheck },
    ],
  },
];

export function MoreScreen() {
  return (
    <div className="animate-fadeUp pb-6">
      <h1 className="px-1 pb-6 pt-2 text-[34px] font-semibold tracking-[-0.03em]">More</h1>
      {GROUPS.map((group) => (
        <section key={group.title} className="mb-8">
          <h2 className="px-1 pb-2 text-[13px] font-medium text-[var(--text-muted)]">{group.title}</h2>
          <div className="-mx-4">
            {group.items.map(({ to, label, icon: Icon }) => (
              <Link
                key={to}
                to={to}
                className="press flex items-center gap-3 border-b border-white/[0.06] px-4 py-3.5 active:bg-white/[0.04]"
              >
                <span className="flex h-9 w-9 items-center justify-center rounded-full bg-white/[0.05] text-[var(--text-secondary)]">
                  <Icon size={18} strokeWidth={1.75} />
                </span>
                <span className="flex-1 text-[16px] font-medium tracking-tight">{label}</span>
                <ChevronRight size={16} className="text-[var(--text-faint)]" />
              </Link>
            ))}
          </div>
        </section>
      ))}
      <button
        onClick={() => {
          try { localStorage.removeItem("fm_token"); } catch { /* noop */ }
          window.location.href = "/";
        }}
        className="press mt-2 flex w-full items-center justify-center gap-2 py-4 text-[16px] font-medium text-[var(--accent-red)]"
      >
        <LogOut size={16} /> Log out
      </button>
    </div>
  );
}
