import { NavLink } from "react-router-dom";
import { CandlestickChart, Home, LayoutGrid, Radio, Wallet } from "lucide-react";

/** Five destinations. Everything else lives on More. */
const tabs = [
  { to: "/", label: "Home", icon: Home },
  { to: "/charts", label: "Markets", icon: CandlestickChart },
  { to: "/signals", label: "Signals", icon: Radio },
  { to: "/positions", label: "Portfolio", icon: Wallet },
  { to: "/more", label: "More", icon: LayoutGrid },
];

export function BottomNav() {
  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-[80] border-t border-white/[0.06] bg-[rgba(7,11,16,0.86)] backdrop-blur-xl lg:hidden"
      style={{ paddingBottom: "env(safe-area-inset-bottom, 0px)" }}
      aria-label="Primary"
    >
      <div className="mx-auto flex h-[52px] w-full max-w-[560px] items-stretch justify-between px-2">
        {tabs.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className="press flex min-h-11 min-w-0 flex-1 flex-col items-center justify-center gap-0.5"
            aria-label={label}
          >
            {({ isActive }) => (
              <>
                <Icon
                  size={22}
                  strokeWidth={isActive ? 2.25 : 1.6}
                  fill={isActive ? "currentColor" : "none"}
                  fillOpacity={isActive ? 0.18 : 0}
                  className={isActive ? "text-[var(--text-primary)]" : "text-[var(--text-faint)]"}
                />
                <span className={`text-[11px] leading-none tracking-tight ${isActive ? "font-semibold text-[var(--text-primary)]" : "font-medium text-[var(--text-faint)]"}`}>
                  {label}
                </span>
              </>
            )}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
