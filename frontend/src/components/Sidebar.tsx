import { NavLink } from "react-router-dom";
import { Home, Radio, Briefcase, GraduationCap, NotebookPen, Settings, PieChart } from "lucide-react";
import { Logo, StatusDot } from "./ui";

const items = [
  { to: "/", label: "Home", icon: Home },
  { to: "/signals", label: "Signals", icon: Radio },
  { to: "/agent", label: "Agent", icon: Briefcase },
  { to: "/learning", label: "Learning Lab", icon: GraduationCap },
  { to: "/journal", label: "Journal", icon: NotebookPen },
  { to: "/analytics", label: "Analytics", icon: PieChart },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  return (
    <aside className="sticky top-0 hidden h-screen w-[250px] shrink-0 flex-col justify-between px-6 py-8 lg:flex">
      <div>
        <div className="mb-10 px-2">
          <Logo size={38} />
        </div>
        <nav className="space-y-1.5">
          {items.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `tap flex items-center gap-3 rounded-2xl border px-4 py-3 text-[13.5px] font-medium transition-all duration-300 ${
                  isActive
                    ? "border-[rgba(var(--p-rgb),0.3)] bg-gradient-to-r from-[rgba(var(--p-rgb),0.22)] to-[rgba(var(--p-rgb),0.06)] text-[var(--text-primary)] glow-blue"
                    : "border-transparent text-[var(--text-muted)] hover:bg-[rgba(var(--warm-rgb),0.045)] hover:text-[var(--text-secondary)]"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <Icon size={17.5} strokeWidth={isActive ? 2.1 : 1.7} style={isActive ? { filter: "drop-shadow(0 0 6px rgba(var(--p-rgb),0.8))" } : undefined} />
                  {label}
                </>
              )}
            </NavLink>
          ))}
        </nav>
      </div>
      <div className="space-y-3 px-2">
        <div className="flex items-center gap-2 text-[11px] text-[var(--text-secondary)]">
          <StatusDot tone="green" /> Agent active
        </div>
        <p className="text-[10px] leading-relaxed text-[var(--text-muted)]">
          Research & signals only - never executes trades.
          <br />v0.1.0 - Trade - Learn - Grow
        </p>
      </div>
    </aside>
  );
}
