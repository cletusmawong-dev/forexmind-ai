import { Link } from "react-router-dom";
import { BarChart3, Bell, BookOpen, ChevronRight, FlaskConical, Layers, LayoutGrid, Newspaper, Settings as SettingsIcon, ShieldCheck, Zap } from "lucide-react";
import { Eyebrow, Glass } from "../components/ui";
import { Logo } from "../components/Logo";

const ITEMS = [
  { to: "/news", label: "News & Events", desc: "Economic calendar + AI explain", icon: Newspaper },
  { to: "/journal", label: "Journal", desc: "Trade log & lessons", icon: BookOpen },
  { to: "/strategies", label: "Strategies", desc: "Live, sleeping & research", icon: Layers },
  { to: "/learning", label: "AI Research Lab", desc: "Experiments & evidence", icon: FlaskConical },
  { to: "/analytics", label: "Analytics", desc: "Performance deep-dive", icon: BarChart3 },
  { to: "/execution", label: "Execution", desc: "VPS / MT5 / connector", icon: Zap },
  { to: "/notifications", label: "Notifications", desc: "History & Telegram", icon: Bell },
  { to: "/admin", label: "Admin", desc: "Users & risk (owner)", icon: ShieldCheck },
  { to: "/settings", label: "Settings", desc: "Accounts, risk, appearance", icon: SettingsIcon },
];

export function MoreScreen() {
  return (
    <div className="animate-fadeUp">
      <header className="mb-7 flex items-start justify-between">
        <div className="flex items-center gap-3.5">
          <span className="icon-chip" aria-hidden="true"><LayoutGrid size={20} /></span>
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight">More</h1>
            <p className="mt-1 text-[12.5px] text-txt-low">Everything in FOREXMIND.</p>
          </div>
        </div>
      </header>
      <Glass pad={false} className="overflow-hidden">
        {ITEMS.map(({ to, label, desc, icon: Icon }, i) => (
          <Link key={to} to={to}
                 className={`flex items-center gap-3.5 px-4 py-3.5 active:bg-[rgba(var(--p-rgb),0.06)] ${i ? "border-t border-[rgba(var(--warm-rgb),0.06)]" : ""}`}>
            <span className="icon-chip shrink-0" aria-hidden="true"><Icon size={17} /></span>
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-semibold text-txt-hi">{label}</span>
              <span className="block text-[10.5px] text-txt-faint">{desc}</span>
            </span>
            <ChevronRight size={15} className="shrink-0 text-txt-faint" />
          </Link>
        ))}
      </Glass>
      <p className="mt-8 pb-4 text-center text-[10px] text-txt-faint">
        <Logo /> FOREXMIND AI - Trade - Learn - Grow
      </p>
    </div>
  );
}
