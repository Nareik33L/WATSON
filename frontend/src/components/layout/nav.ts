export interface NavItem {
  to: string;
  label: string;
  icon: string;
  end?: boolean;
}

export const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "Workspace",
    items: [
      { to: "/", label: "Overview", icon: "overview", end: true },
      { to: "/briefing", label: "Daily Briefing", icon: "briefing" },
      { to: "/deals", label: "Deals", icon: "deals" },
      { to: "/customers", label: "Customers", icon: "customers" },
      { to: "/commitments", label: "Commitments", icon: "commitments" },
      { to: "/approvals", label: "Approvals", icon: "approvals" },
      { to: "/forecast", label: "Forecast", icon: "forecast" },
    ],
  },
  {
    section: "System",
    items: [
      { to: "/files", label: "Files & Reports", icon: "files" },
      { to: "/activity", label: "Activity", icon: "activity" },
      { to: "/integrations", label: "Integrations", icon: "integrations" },
      { to: "/settings", label: "Settings", icon: "settings" },
    ],
  },
];
