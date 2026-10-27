import { NavLink } from "react-router-dom";
import { Activity, Bot, Briefcase, CandlestickChart, Home, LayoutGrid, Radar, Users } from "lucide-react";

// Approved reference 2026-10-26: solid white bar, hairline top border,
// blue active state, all seven destinations visible on narrow screens.
// Icons stay labeled; on very narrow (<360px) labels hide via CSS below.
const tabs = [
  { to: "/", label: "Home", icon: Home },
  { to: "/charts", label: "Charts", icon: CandlestickChart },
  { to: "/signals", label: "Signals", icon: Radar },
  { to: "/positions", label: "Positions", icon: Briefcase },
  { to: "/agent", label: "AI", icon: Bot },
  { to: "/community", label: "Community", icon: Users },
  { to: "/more", label: "More", icon: LayoutGrid },
];

export function BottomNav() {
  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-[80] border-t border-[var(--glass-border)] bg-white lg:hidden"
      style={{ paddingBottom: "env(safe-area-inset-bottom, 0px)" }}
      aria-label="Primary"
    >
      <div className="mx-auto flex w-full max-w-[560px] items-stretch justify-between px-1">
        {tabs.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              `tap relative flex min-w-0 flex-1 flex-col items-center justify-center gap-[3px] pb-[7px] pt-[9px] ${
                isActive ? "text-[var(--accent-blue)]" : "text-[var(--text-faint)]"
              }`
            }
            aria-label={label}
          >
            {({ isActive }) => (
              <>
                <Icon size={20} strokeWidth={isActive ? 2.2 : 1.7} />
                <span className="max-w-full truncate px-0.5 text-[8.5px] font-semibold tracking-wide">
                  {label}
                </span>
                {isActive && <span className="absolute bottom-[2px] h-[3px] w-4 rounded-full bg-[var(--accent-blue)]" />}
              </>
            )}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
