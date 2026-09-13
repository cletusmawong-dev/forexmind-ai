import { NavLink } from "react-router-dom";
import { Home, Radio, Bot, GraduationCap, NotebookPen, Settings, PieChart } from "lucide-react";
import { Logo } from "./Logo";
import { GlowDot } from "./ui";

const items = [
  { to: "/", label: "Home", icon: Home },
  { to: "/signals", label: "Signals", icon: Radio },
  { to: "/agent", label: "Agent", icon: Bot },
  { to: "/learning", label: "Learning Lab", icon: GraduationCap },
  { to: "/journal", label: "Journal", icon: NotebookPen },
  { to: "/analytics", label: "Analytics", icon: PieChart },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  return (
    <aside className="sticky top-0 hidden h-screen w-[248px] shrink-0 flex-col justify-between px-6 py-8 lg:flex">
      <div>
        <div className="mb-10 px-2">
          <Logo size={36} />
        </div>
        <nav className="space-y-1">
          {items.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `tap flex items-center gap-3 rounded-2xl px-4 py-3 text-[13.5px] font-medium transition-all ${
                  isActive ? "bg-white/[0.06] text-txt-hi shadow-soft" : "text-txt-low hover:bg-white/[0.03] hover:text-txt-mid"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <Icon size={17.5} strokeWidth={isActive ? 2 : 1.7} className={isActive ? "text-acc" : ""} />
                  {label}
                </>
              )}
            </NavLink>
          ))}
        </nav>
      </div>
      <div className="space-y-3 px-2">
        <div className="flex items-center gap-2 text-[11px] text-txt-low">
          <GlowDot tone="pos" /> Agent active
        </div>
        <p className="text-[10px] leading-relaxed text-txt-faint">
          Research & signals only — never executes trades.
          <br />
          v0.1.0 · Trade · Learn · Grow
        </p>
      </div>
    </aside>
  );
}
