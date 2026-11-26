import { useState } from "react";
import { Link } from "react-router-dom";
import { ErrorNote, Loading, PageHeader, SourcePill } from "@/components/ui";
import { formatWhen } from "@/lib/format";
import { useApi } from "@/lib/useApi";

interface ActivityItem {
  id: string;
  occurred_at: string | null;
  kind: string;
  source_system: string | null;
  source_label: string;
  title: string;
  summary: string;
  href: string;
}

const KINDS = [
  { id: "all", label: "All" },
  { id: "interaction", label: "Interactions" },
  { id: "approval", label: "Approvals" },
  { id: "sync", label: "Sync" },
  { id: "audit", label: "Audit" },
];

export function ActivityPage() {
  const [kind, setKind] = useState("all");
  const api = useApi<{ items: ActivityItem[] }>(`/api/activity?kind=${kind}`);
  return (
    <div>
      <PageHeader title="Activity" description="Interactions, local decisions, simulated sync, and the audit trail. Nothing here was fetched from another system." />
      <div className="mb-4 flex flex-wrap gap-2">
        {KINDS.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setKind(item.id)}
            className={`rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset ${kind === item.id ? "bg-navy-800 text-white ring-navy-800" : "bg-white ring-line"}`}
          >
            {item.label}
          </button>
        ))}
      </div>
      {api.loading && !api.data ? <Loading label="Loading activity…" /> : null}
      {api.error ? <ErrorNote message={api.error} onRetry={api.reload} /> : null}
      <ol className="space-y-3">
        {api.data?.items.map((item) => (
          <li key={item.id} className="rounded-xl border border-line bg-white px-4 py-3 shadow-card">
            <div className="flex flex-wrap items-center gap-2">
              {item.source_system ? <SourcePill system={item.source_system} label={item.source_label} /> : <span className="text-xs text-muted">{item.source_label}</span>}
              <span className="text-xs text-muted">{formatWhen(item.occurred_at)}</span>
            </div>
            <Link to={item.href} className="mt-1 block text-sm font-medium hover:text-accent">{item.title}</Link>
            <p className="text-sm text-muted">{item.summary}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}
