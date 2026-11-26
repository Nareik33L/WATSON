import { Link } from "react-router-dom";
import { FilterBar } from "@/components/FilterBar";
import { Card, ErrorNote, Loading, Pill, StatusPill } from "@/components/ui";
import { formatDate, formatWhen, gbp, gbpCompact } from "@/lib/format";
import { withQuery } from "@/lib/api";
import { useFilters } from "@/lib/filters";
import type { Approval, Commitment, Deal } from "@/lib/types";
import { useApi } from "@/lib/useApi";

interface OverviewPayload {
  greeting: string;
  date_label: string;
  focus: string;
  method_note: string;
  priorities: { id: string; urgency: string; title: string; context: string; customer_id: string; opportunity_id: string | null; kind: string }[];
  totals: {
    open_count: number;
    pipeline: number;
    crm_weighted: number;
    simulated_weighted: number;
    at_risk: number;
    overdue_commitments: number;
    pending_approvals: number;
    meetings: number;
  };
  attention: Deal[];
  moving: Deal[];
  commitments: Commitment[];
  activity: { id: string; occurred_at: string | null; title: string; summary: string; source_label: string; href: string }[];
  approvals: Approval[];
}

export function Overview() {
  const { query } = useFilters();
  const api = useApi<OverviewPayload>(withQuery("/api/overview", query));
  if (api.loading && !api.data) return <Loading label="Loading the portfolio…" />;
  if (api.error || !api.data) return <ErrorNote message={api.error ?? "The overview could not be loaded."} onRetry={api.reload} />;
  const data = api.data;
  const metrics = [
    { label: "Open opportunities", value: String(data.totals.open_count), hint: "In this view", href: "/deals" },
    { label: "Open pipeline", value: gbpCompact(data.totals.pipeline), hint: "Unweighted value", href: "/deals" },
    { label: "CRM weighted", value: gbpCompact(data.totals.crm_weighted), hint: "Value × CRM probability", href: "/forecast" },
    { label: "Simulated adjusted", value: gbpCompact(data.totals.simulated_weighted), hint: "Placeholder · no model", href: "/forecast", simulated: true },
    { label: "At risk or stalled", value: String(data.totals.at_risk), hint: "Need a decision", href: "/deals" },
    { label: "Overdue promises", value: String(data.totals.overdue_commitments), hint: "Not marked complete", href: "/commitments" },
    { label: "Pending approvals", value: String(data.totals.pending_approvals), hint: "Local decisions only", href: "/approvals" },
    { label: "Meetings, 7 days", value: String(data.totals.meetings), hint: "Including today", href: "/briefing" },
  ];
  return (
    <div>
      <p className="text-sm text-muted">{data.date_label}</p>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight">{data.greeting}</h1>
      <p className="mt-1 max-w-3xl text-sm text-ink">{data.focus}</p>
      <p className="mt-1 text-xs text-muted">{data.method_note}</p>
      <div className="mt-4">
        <FilterBar />
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        {data.priorities.map((item, index) => (
          <Link key={item.id} to={item.opportunity_id ? `/deals/${item.opportunity_id}` : `/customers/${item.customer_id}`} className="rounded-xl border border-line bg-white p-4 shadow-card hover:border-slate-300">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium uppercase tracking-wide text-muted">Focus {index + 1}</span>
              <StatusPill value={item.urgency === "high" ? "Overdue" : "Upcoming"} label={item.urgency} />
            </div>
            <p className="mt-2 text-sm font-medium text-ink">{item.title}</p>
            <p className="mt-1 text-xs text-muted">{item.context}</p>
          </Link>
        ))}
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {metrics.map((metric) => (
          <Link key={metric.label} to={metric.href} className="rounded-xl border border-line bg-white p-4 shadow-card hover:border-slate-300">
            <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-muted">
              {metric.label}
              {metric.simulated ? <Pill tone="simulated">Simulated</Pill> : null}
            </div>
            <div className="mt-2 text-2xl font-semibold tabular-nums">{metric.value}</div>
            <div className="mt-1 text-xs text-muted">{metric.hint}</div>
          </Link>
        ))}
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-5">
        <Card className="xl:col-span-3">
          <h2 className="text-sm font-semibold">Needs attention</h2>
          <ul className="mt-3 divide-y divide-line">
            {data.attention.length === 0 ? <li className="py-6 text-sm text-muted">Nothing in this view is flagged.</li> : null}
            {data.attention.map((deal) => (
              <li key={deal.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
                <div>
                  <Link to={`/deals/${deal.id}`} className="text-sm font-medium text-accent hover:underline">
                    {deal.name}
                  </Link>
                  <div className="text-xs text-muted">{deal.customer_name}</div>
                  <p className="mt-1 text-sm text-ink">{deal.situation}</p>
                </div>
                <div className="text-right">
                  <div className="text-sm font-medium tabular-nums">{gbp(deal.value)}</div>
                  <div className="mt-1 flex justify-end gap-1">
                    <StatusPill value={deal.health} />
                    <StatusPill value={deal.momentum} />
                  </div>
                  <div className="mt-1 text-xs text-muted">{formatDate(deal.close_date)}</div>
                </div>
              </li>
            ))}
          </ul>
        </Card>
        <div className="space-y-4 xl:col-span-2">
          <Card>
            <h2 className="text-sm font-semibold">Promised</h2>
            <ul className="mt-3 space-y-3">
              {data.commitments.length === 0 ? <li className="text-sm text-muted">No commitments are due in this view.</li> : null}
              {data.commitments.map((item) => (
                <li key={item.id}>
                  <div className="flex items-center justify-between gap-2">
                    <StatusPill value={item.status} />
                    <span className="text-xs text-muted">{formatDate(item.due_date)}</span>
                  </div>
                  <p className="mt-1 text-sm">{item.description}</p>
                  <p className="text-xs text-muted">{item.customer_name} · {item.owner_name}</p>
                </li>
              ))}
            </ul>
          </Card>
          <Card>
            <h2 className="text-sm font-semibold">Moving well</h2>
            <ul className="mt-3 space-y-2">
              {data.moving.length === 0 ? <li className="text-sm text-muted">No deals in this view are improving.</li> : null}
              {data.moving.map((deal) => (
                <li key={deal.id}>
                  <Link to={`/deals/${deal.id}`} className="text-sm font-medium text-accent hover:underline">{deal.customer_name}</Link>
                  <p className="text-xs text-muted">{deal.situation}</p>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-5">
        <Card className="xl:col-span-3">
          <h2 className="text-sm font-semibold">Recent customer activity</h2>
          <ul className="mt-3 divide-y divide-line">
            {data.activity.map((item) => (
              <li key={item.id} className="py-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span>{item.source_label}</span>
                  <span>{formatWhen(item.occurred_at)}</span>
                </div>
                <Link to={item.href} className="text-sm font-medium text-ink hover:text-accent">{item.title}</Link>
                <p className="text-sm text-muted">{item.summary}</p>
              </li>
            ))}
          </ul>
        </Card>
        <Card className="xl:col-span-2">
          <h2 className="text-sm font-semibold">Waiting on you</h2>
          <ul className="mt-3 space-y-3">
            {data.approvals.length === 0 ? <li className="text-sm text-muted">No approvals are waiting in this view.</li> : null}
            {data.approvals.map((item) => (
              <li key={item.id}>
                <Link to="/approvals" className="text-sm font-medium text-accent hover:underline">{item.title}</Link>
                <p className="text-xs text-muted">{item.customer_name} · {item.action_label}</p>
              </li>
            ))}
          </ul>
          <p className="mt-4 text-xs text-muted">
            CRM weighted {gbpCompact(data.totals.crm_weighted)}. Simulated placeholder {gbpCompact(data.totals.simulated_weighted)}. Demo data only.
          </p>
        </Card>
      </div>
    </div>
  );
}
