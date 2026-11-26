import { Link, useParams } from "react-router-dom";
import { Timeline } from "@/components/Timeline";
import { Card, ErrorNote, Loading, StatusPill } from "@/components/ui";
import { formatDate, gbp, titleCase } from "@/lib/format";
import type { Commitment, Deal, Intelligence, TimelineItem } from "@/lib/types";
import { useApi } from "@/lib/useApi";

interface CustomerDetailPayload {
  customer: {
    id: string;
    name: string;
    industry: string;
    region: string;
    business_unit: string;
    importance: string;
    summary: string;
    owner_name: string;
    data_origin: string;
  };
  contacts: { id: string; name: string; title: string; email: string; phone: string; active: boolean; note: string }[];
  opportunities: Deal[];
  commitments: Commitment[];
  timeline: TimelineItem[];
  intelligence: Intelligence[];
}

export function CustomerDetail() {
  const { id } = useParams();
  const api = useApi<CustomerDetailPayload>(id ? `/api/customers/${id}` : null);
  if (api.loading && !api.data) return <Loading label="Loading the customer…" />;
  if (api.error || !api.data) return <ErrorNote message={api.error ?? "That customer is not in the demo portfolio."} />;
  const { customer, contacts, opportunities, commitments, timeline, intelligence } = api.data;
  return (
    <div>
      <Link to="/customers" className="text-sm text-accent hover:underline">Back to customers</Link>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">{customer.name}</h1>
      <p className="mt-1 text-sm text-muted">{customer.industry} · {customer.region} · {customer.business_unit} · {customer.importance}</p>
      <p className="mt-2 max-w-3xl text-sm">{customer.summary}</p>
      <p className="mt-1 text-xs text-muted">Owner {customer.owner_name}. Data origin: {customer.data_origin}.</p>
      <div className="mt-4 grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-2">
          <h2 className="text-sm font-semibold">People</h2>
          <ul className="mt-3 space-y-3">
            {contacts.map((person) => (
              <li key={person.id}>
                <div className="text-sm font-medium">{person.name}{person.active ? "" : " · inactive"}</div>
                <div className="text-xs text-muted">{person.title}</div>
                <div className="text-xs text-muted">{person.email}</div>
                <div className="text-xs text-muted">{person.phone}</div>
                {person.note ? <p className="text-xs text-muted">{person.note}</p> : null}
              </li>
            ))}
          </ul>
        </Card>
        <Card className="lg:col-span-3">
          <h2 className="text-sm font-semibold">Opportunities</h2>
          <ul className="mt-3 divide-y divide-line">
            {opportunities.map((deal) => (
              <li key={deal.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
                <div>
                  <Link to={`/deals/${deal.id}`} className="text-sm font-medium text-accent hover:underline">{deal.name}</Link>
                  <p className="text-sm text-muted">{deal.situation}</p>
                </div>
                <div className="text-right">
                  <div className="text-sm font-medium tabular-nums">{gbp(deal.value)}</div>
                  <div className="mt-1"><StatusPill value={deal.stage} label={deal.stage} /></div>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <Card className="mt-4">
        <h2 className="text-sm font-semibold">Commitments</h2>
        <ul className="mt-3 space-y-2">
          {commitments.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm">{item.description}</span>
              <span className="flex items-center gap-2 text-xs text-muted"><StatusPill value={item.status} />{formatDate(item.due_date)}</span>
            </li>
          ))}
        </ul>
      </Card>
      <Card className="mt-4">
        <h2 className="text-sm font-semibold">Timeline</h2>
        <div className="mt-3"><Timeline items={timeline} /></div>
      </Card>
      <Card className="mt-4">
        <h2 className="text-sm font-semibold">Notes on the account</h2>
        <ul className="mt-3 space-y-2">
          {intelligence.map((item) => (
            <li key={item.id} className="text-sm">
              <span className="font-medium">{titleCase(item.category)}. </span>
              {item.summary}
              {item.simulated ? <span className="ml-2 text-xs text-accent">Simulated placeholder</span> : null}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
