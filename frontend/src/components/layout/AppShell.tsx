import { Menu } from "lucide-react";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "@/components/layout/Sidebar";
import { useApi } from "@/lib/useApi";
import type { Meta } from "@/lib/types";

interface WorkspaceValue {
  meta: Meta | null;
  reloadMeta: () => void;
}

const WorkspaceContext = createContext<WorkspaceValue | null>(null);

export function useWorkspace(): WorkspaceValue {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("Workspace is only available inside the app shell.");
  return value;
}

const TITLES: [string, string][] = [
  ["/deals/", "Opportunity"],
  ["/customers/", "Customer"],
  ["/briefing", "Daily Briefing"],
  ["/deals", "Deals"],
  ["/customers", "Customers"],
  ["/commitments", "Commitments"],
  ["/approvals", "Approvals"],
  ["/forecast", "Forecast"],
  ["/files", "Files & Reports"],
  ["/activity", "Activity"],
  ["/integrations", "Integrations"],
  ["/settings", "Settings"],
];

function pageTitle(pathname: string): string {
  if (pathname === "/") return "Overview";
  const match = TITLES.find(([prefix]) => pathname.startsWith(prefix));
  return match?.[1] ?? "Command Centre";
}

export function AppShell() {
  const location = useLocation();
  const metaApi = useApi<Meta>("/api/meta");
  const [open, setOpen] = useState(false);
  const workspace = useMemo<WorkspaceValue>(
    () => ({ meta: metaApi.data, reloadMeta: metaApi.reload }),
    [metaApi.data, metaApi.reload],
  );

  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    document.title = `${pageTitle(location.pathname)} · Command Centre`;
  }, [location.pathname]);

  return (
    <WorkspaceContext.Provider value={workspace}>
      <div className="min-h-screen bg-canvas text-ink">
        <a href="#content" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2">
          Skip to content
        </a>
        <div className="hidden lg:fixed lg:inset-y-0 lg:flex lg:w-64">
          <Sidebar pending={metaApi.data?.counts.pending_approvals ?? 0} />
        </div>
        {open ? (
          <div className="fixed inset-0 z-40 lg:hidden">
            <button type="button" className="absolute inset-0 bg-navy-950/40" aria-label="Close menu" onClick={() => setOpen(false)} />
            <Sidebar pending={metaApi.data?.counts.pending_approvals ?? 0} onNavigate={() => setOpen(false)} className="relative z-10" />
          </div>
        ) : null}
        <div className="lg:pl-64">
          <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-white/95 px-4 backdrop-blur">
            <button
              type="button"
              className="grid h-9 w-9 place-items-center rounded-md text-ink hover:bg-slate-100 lg:hidden"
              aria-label="Open menu"
              onClick={() => setOpen(true)}
            >
              <Menu className="h-5 w-5" />
            </button>
            <div className="text-sm font-medium text-ink">{pageTitle(location.pathname)}</div>
            <div className="ml-auto hidden text-sm text-muted sm:block">{metaApi.data?.user.name ?? "Alex Harrington"}</div>
          </header>
          <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-[13px] text-amber-950" data-testid="demo-banner">
            {metaApi.data?.disclaimer ??
              "Fictional demo data. Outlook, Gong, HubSpot, Excel, and Calendly are not connected. No AI model is running."}
          </div>
          <main id="content" className="mx-auto w-full max-w-[1400px] px-4 py-6 sm:px-6">
            <Outlet />
          </main>
        </div>
      </div>
    </WorkspaceContext.Provider>
  );
}
