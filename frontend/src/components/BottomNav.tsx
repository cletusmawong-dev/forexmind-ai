import { NavLink } from "react-router-dom";
import { Home, Radio, Briefcase, GraduationCap, NotebookPen, Settings } from "lucide-react";

const tabs = [
  { to: "/", label: "Home", icon: Home },
  { to: "/signals", label: "Signals", icon: Radio },
  { to: "/agent", label: "Agent", icon: Briefcase },
  { to: "/learning", label: "Learn", icon: GraduationCap },
  { to: "/journal", label: "Journal", icon: NotebookPen },
  { to: "/settings", label: "Settings", icon: Settings },
];

/** Floating liquid-glass dock (matches the reference mockup). */
export function BottomNav() {
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-[80] flex justify-center px-4 lg:hidden" style={{ paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 14px)" }}>
      <nav className="glass-float pointer-events-auto flex items-center gap-0.5 rounded-[26px] px-2 py-2" aria-label="Primary">
        {tabs.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              `tap relative flex h-[52px] w-[52px] flex-col items-center justify-center gap-[3px] rounded-[18px] transition-all duration-300 ${
                isActive
                  ? "bg-gradient-to-b from-[rgba(var(--p2-rgb),0.5)] to-[rgba(var(--p-rgb),0.14)] text-[var(--text-primary)] shadow-[0_0_22px_rgba(var(--p-rgb),0.4),inset_0_1px_0_rgba(255,255,255,0.3)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
              }`
            }
            aria-label={label}
          >
            {({ isActive }) => (
              <>
                <Icon
                  size={19}
                  strokeWidth={isActive ? 2.2 : 1.7}
                  style={isActive ? { filter: "drop-shadow(0 0 8px rgba(var(--p-rgb),0.9))" } : undefined}
                />
                <span className={`text-[8px] font-semibold tracking-wide ${isActive ? "text-[var(--text-primary)]" : ""}`}>{label}</span>
                {isActive && (
                  <span
                    className="absolute bottom-[3px] h-[3px] w-5 rounded-full bg-gradient-to-r from-[#3e7bfa] to-[#33d6f6]"
                    style={{ boxShadow: "0 0 10px rgba(var(--p-rgb),0.9)" }}
                  />
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
