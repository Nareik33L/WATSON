import { Link, useParams } from "react-router-dom";
import { Timeline } from "@/components/Timeline";
import { Card, ErrorNote, Loading, Notice, Pill, StatusPill } from "@/components/ui";
import { formatDate, formatWhen, gbp, titleCase } from "@/lib/format";
import type { Commitment, Deal, Intelligence, TimelineItem } from "@/lib/types";
import { useApi } from "@/lib/useApi";

interface DealDetailPayload {
  deal: Deal;
  stakeholders: { id: string; name: string; title: string; email: string; phone: string; role: string; is_primary: boolean; active: boolean; note: string }[];
  commitments: Commitment[];
  timeline: TimelineItem[];
  intelligence: Intelligence[];
  simulated_forecast: { label: string; probability: number; confidence: string | null; assumption: string | null; disclaimer: string } | null;
}

export function DealDetail() {
  const { id } = useParams();
  const api = useApi<DealDetailPayload>(id ? `/api/deals/${id}` : null);
  if (api.loading && !api.data) return <Loading label="Loading the opportunity…" />;
  if (api.error || !api.data) return <ErrorNote message={api.error ?? "That opportunity is not in the demo portfolio."} />;
  const { deal, stakeholders, commitments, timeline, intelligence, simulated_forecast: forecast } = api.data;
  const records = intelligence.filter((item) => !item.simulated);
  const placeholders = intelligence.filter((item) => item.simulated);
  return (
    <div>
      <Link to="/deals" className="text-sm text-accent hover:underline">Back to deals</Link>
      <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{deal.name}</h1>
          <Link to={`/customers/${deal.customer_id}`} className="text-sm text-accent hover:underline" data-testid={`customer-${deal.customer_id}`}>
            {deal.customer_name}
          </Link>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold tabular-nums">{gbp(deal.value)}</div>
          <div className="mt-1 flex justify-end gap-1">
            <StatusPill value={deal.stage} label={deal.stage} />
            <StatusPill value={deal.health} />
          </div>
        </div>
      </div>
      <p className="mt-3 max-w-3xl text-sm text-ink">{deal.situation}</p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Close date" value={formatDate(deal.close_date)} />
        <Fact label="Owner" value={deal.owner_name} />
        <Fact label="Product" value={deal.product} />
        <Fact label="Pipeline" value={`${deal.pipeline} · ${deal.forecast_category}`} />
        <Fact label="CRM probability" value={`${deal.crm_probability}%`} />
        <Fact label="Momentum" value={deal.momentum} />
        <Fact label="Last activity" value={formatWhen(deal.last_interaction_at)} />
        <Fact label="Next activity" value={formatWhen(deal.next_interaction_at)} />
      </div>
      <p className="mt-3 text-sm text-ink"><span className="font-medium">Next step. </span>{deal.next_step}</p>
      {forecast ? (
        <div className="mt-4 rounded-xl border border-dashed border-accent/40 bg-white p-4">
          <div className="flex items-center gap-2">
            <Pill tone="simulated">Simulated placeholder</Pill>
            <span className="text-xs text-muted">{forecast.label}</span>
          </div>
          <p className="mt-2 text-lg font-semibold tabular-nums">{forecast.probability}% win probability</p>
          <p className="text-sm text-muted">Confidence: {forecast.confidence ?? "not set"}. {forecast.assumption}</p>
          <p className="mt-2 text-xs text-muted">{forecast.disclaimer}</p>
        </div>
      ) : (
        <Notice>No simulated probability is stored for this closed deal.</Notice>
      )}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="text-sm font-semibold">Stakeholders</h2>
          <ul className="mt-3 space-y-3">
            {stakeholders.map((person) => (
              <li key={person.id}>
                <div className="text-sm font-medium">{person.name}{person.active ? "" : " · left the business"}</div>
                <div className="text-xs text-muted">{person.title} · {person.role}{person.is_primary ? " · primary" : ""}</div>
                <div className="text-xs text-muted">{person.email} · {person.phone}</div>
                {person.note ? <p className="text-xs text-muted">{person.note}</p> : null}
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <h2 className="text-sm font-semibold">Commitments</h2>
          <ul className="mt-3 space-y-3">
            {commitments.length === 0 ? <li className="text-sm text-muted">None on this opportunity.</li> : null}
            {commitments.map((item) => (
              <li key={item.id}>
                <StatusPill value={item.status} />
                <p className="mt-1 text-sm">{item.description}</p>
                <p className="text-xs text-muted">{item.owner_name} · due {formatDate(item.due_date)}</p>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <Card className="mt-4">
        <h2 className="text-sm font-semibold">Timeline</h2>
        <div className="mt-3">
          <Timeline items={timeline} />
        </div>
      </Card>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="text-sm font-semibold">Demo records</h2>
          <p className="mt-1 text-xs text-muted">Fictional notes stored with the portfolio. Not produced by a model.</p>
          <ul className="mt-3 space-y-2">
            {records.map((item) => (
              <li key={item.id} className="text-sm"><span className="font-medium">{titleCase(item.category)}. </span>{item.summary}</li>
            ))}
          </ul>
        </Card>
        <Card>
          <h2 className="text-sm font-semibold">Simulated placeholders</h2>
          <p className="mt-1 text-xs text-muted">Labelled placeholders. No model was called.</p>
          <ul className="mt-3 space-y-2">
            {placeholders.map((item) => (
              <li key={item.id} className="text-sm">{item.summary}</li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-white px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 text-sm font-medium">{value}</div>
    </div>
  );
}
