import {
  Activity,
  Briefcase,
  Building2,
  LineChart,
  FolderOpen,
  Inbox,
  LayoutDashboard,
  ListChecks,
  Plug,
  Settings,
  SunMedium,
  type LucideIcon,
} from "lucide-react";
import { NavLink } from "react-router-dom";
import { NAV } from "@/components/layout/nav";
import { cn } from "@/lib/utils";

const ICONS: Record<string, LucideIcon> = {
  overview: LayoutDashboard,
  briefing: SunMedium,
  deals: Briefcase,
  customers: Building2,
  commitments: ListChecks,
  approvals: Inbox,
  forecast: LineChart,
  files: FolderOpen,
  activity: Activity,
  integrations: Plug,
  settings: Settings,
};

export function Sidebar({
  pending,
  onNavigate,
  className,
}: {
  pending: number;
  onNavigate?: () => void;
  className?: string;
}) {
  return (
    <aside className={cn("flex h-full w-64 flex-col bg-navy-900 text-[#d5deea]", className)}>
      <div className="flex items-center gap-3 px-4 py-5">
        <div className="grid h-9 w-9 place-items-center rounded-lg bg-white/10 text-sm font-semibold text-white ring-1 ring-white/15">
          CC
        </div>
        <div>
          <div className="text-sm font-semibold text-white">Command Centre</div>
          <div className="text-[11px] text-[#9fb4cc]">Helios Industrial · Demo</div>
        </div>
      </div>
      <nav className="flex-1 space-y-5 overflow-y-auto px-3 pb-4" aria-label="Primary">
        {NAV.map((group) => (
          <div key={group.section}>
            <div className="px-2 pb-1 text-[11px] font-medium uppercase tracking-wider text-[#9fb4cc]">{group.section}</div>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const Icon = ICONS[item.icon] ?? LayoutDashboard;
                return (
                  <li key={item.to}>
                    <NavLink
                      to={item.to}
                      end={item.end}
                      data-testid={`nav-${item.icon}`}
                      onClick={onNavigate}
                      className={({ isActive }) =>
                        cn(
                          "flex items-center gap-2 rounded-md px-2 py-2 text-sm text-[#e6edf6]",
                          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#8eb4ea]",
                          isActive ? "bg-white/10 font-medium text-white" : "hover:bg-white/5",
                        )
                      }
                    >
                      <Icon className="h-4 w-4 shrink-0 text-[#b7c6d9]" aria-hidden="true" />
                      <span className="flex-1">{item.label}</span>
                      {item.icon === "approvals" && pending > 0 ? (
                        <span className="rounded-full bg-white/15 px-1.5 text-[11px] tabular-nums text-white">{pending}</span>
                      ) : null}
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
      <div className="border-t border-white/10 px-4 py-3 text-[11px] text-[#9fb4cc]">Phase 1 · Local only · No model</div>
    </aside>
  );
}
