import { useState } from "react";
import { Link } from "react-router-dom";
import { FilterBar } from "@/components/FilterBar";
import { Button, Card, Dialog, ErrorNote, Input, Loading, Notice, PageHeader, Select, StatusPill, TextArea } from "@/components/ui";
import { sendJson } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useFilters } from "@/lib/filters";
import type { Commitment, FilterOptions } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const STATUSES = ["New", "Upcoming", "Due today", "Overdue", "Completed", "Cancelled", "Unclear"];

export function Commitments() {
  const { query, params, setParams } = useFilters();
  const status = params.get("status") ?? "";
  const search = new URLSearchParams(query);
  if (status) search.set("status", status);
  const api = useApi<{ items: Commitment[]; counts: Record<string, number> }>(`/api/commitments?${search.toString()}`);
  const options = useApi<FilterOptions>("/api/filters");
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [form, setForm] = useState({
    description: "",
    customer_id: "",
    opportunity_id: "",
    owner_name: "Alex Harrington",
    owner_side: "salesperson",
    importance: "medium",
    due_date: "",
    status: "New",
  });

  async function complete(item: Commitment) {
    setMessage("");
    await sendJson(`/api/commitments/${item.id}`, "PATCH", { status: "Completed", note: "Confirmed in the command centre." });
    setMessage("Marked complete locally. The due date did not do that.");
    api.reload();
  }

  async function create() {
    await sendJson("/api/commitments", "POST", { ...form, opportunity_id: form.opportunity_id || null });
    setOpen(false);
    setMessage("Commitment added locally.");
    api.reload();
  }

  return (
    <div>
      <PageHeader
        title="Commitments"
        description="Open items take Overdue and Due today from the date. Completed, Cancelled, and Unclear stay until you change them."
        actions={<Button variant="primary" onClick={() => setOpen(true)}>Add commitment</Button>}
      />
      <FilterBar />
      {message ? <div className="mb-3"><Notice tone="good">{message}</Notice></div> : null}
      <div className="mb-3 flex flex-wrap gap-2">
        <Chip label="All" count={Object.values(api.data?.counts ?? {}).reduce((sum, value) => sum + value, 0)} active={status === ""} onClick={() => setStatus(params, setParams, "")} />
        {STATUSES.map((item) => (
          <Chip key={item} label={item} count={api.data?.counts[item] ?? 0} active={status === item} onClick={() => setStatus(params, setParams, item)} />
        ))}
      </div>
      <Card>
        {api.loading && !api.data ? <Loading label="Loading commitments…" /> : null}
        {api.error ? <ErrorNote message={api.error} onRetry={api.reload} /> : null}
        <ul className="divide-y divide-line">
          {api.data?.items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill value={item.status} />
                  <span className="text-xs uppercase tracking-wide text-muted">{item.importance}</span>
                </div>
                <p className="mt-1 text-sm">{item.description}</p>
                <p className="text-xs text-muted">
                  {item.owner_name} ({item.owner_side}) · due {formatDate(item.due_date)} ·{" "}
                  <Link to={`/customers/${item.customer_id}`} className="text-accent hover:underline">{item.customer_name}</Link>
                </p>
                {item.evidence ? <p className="mt-1 text-xs text-muted">{item.evidence}</p> : null}
              </div>
              {item.status !== "Completed" && item.status !== "Cancelled" ? (
                <Button onClick={() => void complete(item)}>Mark complete</Button>
              ) : null}
            </li>
          ))}
        </ul>
        {api.data && api.data.items.length === 0 ? <p className="py-6 text-center text-sm text-muted">No commitments match.</p> : null}
      </Card>
      <Dialog open={open} title="Add a commitment" onClose={() => setOpen(false)}>
        <div className="space-y-3">
          <TextArea value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} placeholder="What was promised?" />
          <Select aria-label="Customer" value={form.customer_id} onChange={(event) => setForm({ ...form, customer_id: event.target.value })}>
            <option value="">Customer</option>
            {options.data?.customers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </Select>
          <Select aria-label="Opportunity" value={form.opportunity_id} onChange={(event) => setForm({ ...form, opportunity_id: event.target.value })}>
            <option value="">Opportunity, if any</option>
            {options.data?.opportunities.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </Select>
          <Input aria-label="Owner" value={form.owner_name} onChange={(event) => setForm({ ...form, owner_name: event.target.value })} />
          <div className="grid grid-cols-2 gap-2">
            <Select aria-label="Side" value={form.owner_side} onChange={(event) => setForm({ ...form, owner_side: event.target.value })}>
              <option value="salesperson">Salesperson</option>
              <option value="customer">Customer</option>
              <option value="stakeholder">Stakeholder</option>
            </Select>
            <Select aria-label="Importance" value={form.importance} onChange={(event) => setForm({ ...form, importance: event.target.value })}>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </Select>
          </div>
          <Input type="date" aria-label="Due date" value={form.due_date} onChange={(event) => setForm({ ...form, due_date: event.target.value })} />
          <div className="flex justify-end gap-2">
            <Button onClick={() => setOpen(false)}>Cancel</Button>
            <Button variant="primary" onClick={() => void create()} disabled={form.description.trim().length < 3 || !form.customer_id || !form.due_date}>
              Save
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}

function setStatus(params: URLSearchParams, setParams: (next: URLSearchParams, opts: { replace: boolean }) => void, status: string) {
  const next = new URLSearchParams(params);
  if (status) next.set("status", status);
  else next.delete("status");
  setParams(next, { replace: true });
}

function Chip({ label, count, active, onClick }: { label: string; count: number; active: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className={`rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset ${active ? "bg-navy-800 text-white ring-navy-800" : "bg-white text-ink ring-line"}`}>
      {label} <span className="tabular-nums">{count}</span>
    </button>
  );
}
