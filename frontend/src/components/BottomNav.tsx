import { NavLink } from "react-router-dom";
import { Home, Radio, Bot, GraduationCap, NotebookPen, Settings } from "lucide-react";

const tabs = [
  { to: "/", label: "Home", icon: Home },
  { to: "/signals", label: "Signals", icon: Radio },
  { to: "/agent", label: "Agent", icon: Bot },
  { to: "/learning", label: "Learn", icon: GraduationCap },
  { to: "/journal", label: "Journal", icon: NotebookPen },
  { to: "/settings", label: "", icon: Settings },
];

export function BottomNav() {
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-40 flex justify-center pb-5 lg:hidden">
      <nav className="glass-float pointer-events-auto flex items-center gap-1 rounded-full px-2 py-2" style={{ marginBottom: "env(safe-area-inset-bottom)" }}>
        {tabs.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              `tap relative flex h-11 w-[52px] flex-col items-center justify-center gap-[3px] rounded-full ${
                isActive ? "text-acc" : "text-txt-faint hover:text-txt-mid"
              }`
            }
          >
            {({ isActive }) => (
              <>
                <Icon size={19} strokeWidth={isActive ? 2.1 : 1.7} style={isActive ? { filter: "drop-shadow(0 0 8px rgba(108,158,255,0.7))" } : undefined} />
                {label && <span className="text-[8px] font-semibold tracking-wide">{label}</span>}
                {isActive && (
                  <span className="absolute -bottom-[1px] h-[3px] w-[3px] rounded-full bg-acc" style={{ boxShadow: "0 0 8px rgba(108,158,255,0.9)" }} />
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
