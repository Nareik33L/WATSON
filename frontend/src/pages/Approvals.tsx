import { useState } from "react";
import { Link } from "react-router-dom";
import { FilterBar } from "@/components/FilterBar";
import { useWorkspace } from "@/components/layout/AppShell";
import { Button, Card, ErrorNote, Loading, Notice, PageHeader, SourcePill, StatusPill, TextArea } from "@/components/ui";
import { sendJson, withQuery } from "@/lib/api";
import { useFilters } from "@/lib/filters";
import type { Approval } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const TABS = [
  { id: "pending", label: "Pending" },
  { id: "deferred", label: "Deferred" },
  { id: "decided", label: "Decided" },
];

export function Approvals() {
  const { query, params, setParams } = useFilters();
  const { reloadMeta } = useWorkspace();
  const tab = params.get("tab") ?? "pending";
  const search = new URLSearchParams(query);
  search.set("status", tab);
  const api = useApi<{ items: Approval[]; counts: Record<string, number> }>(withQuery("/api/approvals", search.toString()));
  const [selected, setSelected] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState(false);
  const [proposed, setProposed] = useState("");
  const [banner, setBanner] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const items = api.data?.items ?? [];
  const current = items.find((item) => item.id === selected) ?? items[0];

  function chooseTab(next: string) {
    const copy = new URLSearchParams(params);
    if (next === "pending") copy.delete("tab");
    else copy.set("tab", next);
    setParams(copy, { replace: true });
    setSelected(null);
    setBanner("");
  }

  async function decide(action: "approve" | "reject" | "defer" | "edit") {
    if (!current) return;
    setBusy(true);
    setError("");
    try {
      const result = await sendJson<Approval>(`/api/approvals/${current.id}/decision`, "POST", {
        action,
        note,
        proposed_value: action === "edit" ? proposed : null,
      });
      setBanner(result.result_note);
      setEditing(false);
      setNote("");
      api.reload();
      reloadMeta();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The decision was not saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Approvals"
        description="Every decision is stored on this machine. Approving does not change HubSpot, Outlook, Excel, or the calendar, and it never sends an email."
      />
      <FilterBar />
      <div className="mb-3 flex gap-2">
        {TABS.map((item) => (
          <button key={item.id} type="button" onClick={() => chooseTab(item.id)} className={`rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset ${tab === item.id ? "bg-navy-800 text-white ring-navy-800" : "bg-white ring-line"}`}>
            {item.label} {api.data ? api.data.counts[item.id] : ""}
          </button>
        ))}
      </div>
      {banner ? <div className="mb-3"><Notice tone="good">{banner}</Notice></div> : null}
      {error ? <div className="mb-3"><ErrorNote message={error} /></div> : null}
      {api.loading && !api.data ? <Loading label="Loading approvals…" /> : null}
      {api.error ? <ErrorNote message={api.error} onRetry={api.reload} /> : null}
      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <Card className="p-2">
          {items.length === 0 ? <p className="p-3 text-sm text-muted">Nothing in this tray.</p> : null}
          <ul>
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  data-testid={`approval-${item.id}`}
                  onClick={() => {
                    setSelected(item.id);
                    setProposed(item.proposed_value);
                    setEditing(false);
                    setBanner("");
                  }}
                  className={`w-full rounded-lg px-3 py-2 text-left ${current?.id === item.id ? "bg-accent-soft" : "hover:bg-slate-50"}`}
                >
                  <div className="text-sm font-medium">{item.title}</div>
                  <div className="text-xs text-muted">{item.customer_name} · {item.action_label}</div>
                </button>
              </li>
            ))}
          </ul>
        </Card>
        {current ? (
          <Card>
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill value={current.status} />
              <SourcePill system={current.source_label.toLowerCase()} label={current.source_label} />
            </div>
            <h2 className="mt-2 text-lg font-semibold">{current.title}</h2>
            <p className="text-sm text-muted">{current.proposed_action}</p>
            <p className="mt-1 text-sm">
              {current.customer_id ? <Link to={`/customers/${current.customer_id}`} className="text-accent hover:underline">{current.customer_name}</Link> : null}
              {current.opportunity_id ? <> · <Link to={`/deals/${current.opportunity_id}`} className="text-accent hover:underline">{current.opportunity_name}</Link></> : null}
            </p>
            <dl className="mt-4 grid gap-3 sm:grid-cols-2">
              <div>
                <dt className="text-[11px] uppercase tracking-wide text-muted">Current</dt>
                <dd className="mt-1 text-sm">{current.current_value}</dd>
              </div>
              <div>
                <dt className="text-[11px] uppercase tracking-wide text-muted">Proposed</dt>
                <dd className="mt-1 text-sm">{current.proposed_value}</dd>
              </div>
            </dl>
            <p className="mt-3 text-sm"><span className="font-medium">Why. </span>{current.reason}</p>
            <p className="mt-2 text-sm text-muted"><span className="font-medium text-ink">Evidence. </span>{current.evidence}</p>
            <p className="mt-2 text-sm text-muted"><span className="font-medium text-ink">Impact. </span>{current.impact}</p>
            <div className="mt-3"><Notice tone="warn">{current.constraint_note}</Notice></div>
            {current.decision_note ? <p className="mt-3 text-sm text-muted">Note: {current.decision_note}{current.decided_by ? ` · ${current.decided_by}` : ""}</p> : null}
            {current.status === "pending" || current.status === "deferred" ? (
              <div className="mt-4 space-y-3">
                <TextArea value={note} onChange={(event) => setNote(event.target.value)} placeholder="Optional note, stored locally" />
                {editing ? (
                  <TextArea value={proposed} onChange={(event) => setProposed(event.target.value)} aria-label="Revised value" />
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <Button variant="primary" data-testid="approval-approve" disabled={busy} onClick={() => void decide("approve")}>Approve</Button>
                  <Button variant="danger" disabled={busy} onClick={() => void decide("reject")}>Reject</Button>
                  <Button disabled={busy} onClick={() => { setEditing(true); setProposed(current.proposed_value); if (editing) void decide("edit"); }}>
                    {editing ? "Save edit" : "Edit"}
                  </Button>
                  <Button disabled={busy} onClick={() => void decide("defer")}>Defer</Button>
                </div>
              </div>
            ) : null}
          </Card>
        ) : null}
      </div>
    </div>
  );
}
