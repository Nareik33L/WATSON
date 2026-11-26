import { Link } from "react-router-dom";
import { FilterBar } from "@/components/FilterBar";
import { Card, ErrorNote, Loading, PageHeader, SourcePill, StatusPill } from "@/components/ui";
import { withQuery } from "@/lib/api";
import { formatWhen } from "@/lib/format";
import { useFilters } from "@/lib/filters";
import type { Commitment } from "@/lib/types";
import { useApi } from "@/lib/useApi";

interface BriefingPayload {
  method_note: string;
  filters_active: boolean;
  priorities: { id: string; urgency: string; title: string; context: string; kind: string; customer_id: string; opportunity_id: string | null }[];
  meetings: {
    id: string;
    time_label: string;
    title: string;
    summary: string;
    prep: string;
    customer_id: string;
    customer_name: string;
    opportunity_id: string | null;
    participants: string[];
    source_label: string;
    source_system: string;
    commitments: string[];
  }[];
  follow_ups: Commitment[];
  changes: { id: string; occurred_at: string | null; summary: string; customer_name: string; customer_id: string; source_system: string; source_label: string; kind: string }[];
  alerts: { id: string; urgency: string; label: string; summary: string; customer_name: string; opportunity_id: string; opportunity_name: string }[];
}

export function Briefing() {
  const { query } = useFilters();
  const api = useApi<BriefingPayload>(withQuery("/api/briefing", query));
  if (api.loading && !api.data) return <Loading label="Preparing the briefing…" />;
  if (api.error || !api.data) return <ErrorNote message={api.error ?? "The briefing could not be loaded."} onRetry={api.reload} />;
  const data = api.data;
  return (
    <div>
      <PageHeader title="Daily briefing" description={data.method_note} />
      <FilterBar />
      {data.filters_active ? <p className="mb-3 text-xs text-muted">This briefing is using the active filters.</p> : null}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="text-sm font-semibold">Today’s priorities</h2>
          <ol className="mt-3 space-y-3">
            {data.priorities.map((item, index) => (
              <li key={item.id} className="flex gap-3">
                <span className="mt-0.5 text-sm font-semibold tabular-nums text-muted">{index + 1}</span>
                <div>
                  <StatusPill value={item.urgency === "high" ? "Due today" : "Upcoming"} label={item.urgency} />
                  <Link to={item.opportunity_id ? `/deals/${item.opportunity_id}` : `/customers/${item.customer_id}`} className="mt-1 block text-sm font-medium text-ink hover:text-accent">
                    {item.title}
                  </Link>
                  <p className="text-xs text-muted">{item.context}</p>
                </div>
              </li>
            ))}
          </ol>
        </Card>
        <Card>
          <h2 className="text-sm font-semibold">Today’s meetings</h2>
          {data.meetings.length === 0 ? <p className="mt-3 text-sm text-muted">No meetings on today’s demo calendar.</p> : null}
          <ul className="mt-3 space-y-4">
            {data.meetings.map((meeting) => (
              <li key={meeting.id} className="border-t border-line pt-3 first:border-0 first:pt-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold tabular-nums">{meeting.time_label}</span>
                  <SourcePill system={meeting.source_system} label={meeting.source_label} />
                </div>
                <Link to={meeting.opportunity_id ? `/deals/${meeting.opportunity_id}` : `/customers/${meeting.customer_id}`} className="mt-1 block text-sm font-medium hover:text-accent">
                  {meeting.customer_name} · {meeting.title}
                </Link>
                <p className="text-xs text-muted">{meeting.participants.join(", ")}</p>
                <p className="mt-1 text-sm text-muted">{meeting.summary}</p>
                {meeting.prep ? <p className="mt-2 rounded-md bg-canvas px-2 py-1.5 text-sm text-ink">Preparation notes · demo copy. {meeting.prep}</p> : null}
                {meeting.commitments.length > 0 ? (
                  <ul className="mt-2 list-disc pl-4 text-xs text-muted">
                    {meeting.commitments.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="text-sm font-semibold">Follow-ups</h2>
          <ul className="mt-3 space-y-3">
            {data.follow_ups.map((item) => (
              <li key={item.id} className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm">{item.description}</p>
                  <p className="text-xs text-muted">{item.customer_name} · {item.owner_name}</p>
                </div>
                <StatusPill value={item.status} />
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <h2 className="text-sm font-semibold">Changes since the last look</h2>
          <ul className="mt-3 space-y-3">
            {data.changes.map((item) => (
              <li key={item.id}>
                <div className="flex flex-wrap items-center gap-2">
                  <SourcePill system={item.source_system} label={item.source_label} />
                  <span className="text-xs text-muted">{formatWhen(item.occurred_at)}</span>
                </div>
                <Link to={`/customers/${item.customer_id}`} className="text-sm font-medium hover:text-accent">{item.customer_name}</Link>
                <p className="text-sm text-muted">{item.summary}</p>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <Card className="mt-4">
        <h2 className="text-sm font-semibold">Deal alerts</h2>
        <ul className="mt-3 grid gap-3 md:grid-cols-2">
          {data.alerts.map((alert) => (
            <li key={alert.id} className="rounded-lg border border-line p-3">
              <StatusPill value={alert.urgency === "high" ? "at_risk" : "watch"} label={alert.label} />
              <Link to={`/deals/${alert.opportunity_id}`} className="mt-2 block text-sm font-medium hover:text-accent">
                {alert.customer_name}
              </Link>
              <p className="text-sm text-muted">{alert.summary}</p>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
