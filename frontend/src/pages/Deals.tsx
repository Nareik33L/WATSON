import { Link } from "react-router-dom";
import { DataTable } from "@/components/DataTable";
import { FilterBar } from "@/components/FilterBar";
import { Button, Card, ErrorNote, Loading, PageHeader, StatusPill } from "@/components/ui";
import { withQuery } from "@/lib/api";
import { formatDate, gbp } from "@/lib/format";
import { useFilters } from "@/lib/filters";
import type { Deal } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export function Deals() {
  const { query, params, setParams } = useFilters();
  const scope = params.get("scope") === "all" ? "all" : "open";
  const search = [query, scope === "all" ? "scope=all" : ""].filter(Boolean).join("&");
  const api = useApi<{ items: Deal[] }>(withQuery("/api/deals", search));

  function setScope(next: "open" | "all") {
    const copy = new URLSearchParams(params);
    if (next === "all") copy.set("scope", "all");
    else copy.delete("scope");
    setParams(copy, { replace: true });
  }

  return (
    <div>
      <PageHeader
        title="Deals"
        description="Open work first. Closed deals stay available when you need the history."
        actions={
          <div className="flex gap-2">
            <Button variant={scope === "open" ? "primary" : "secondary"} onClick={() => setScope("open")}>
              Open
            </Button>
            <Button variant={scope === "all" ? "primary" : "secondary"} onClick={() => setScope("all")}>
              All
            </Button>
          </div>
        }
      />
      <FilterBar />
      <Card className="p-0">
        {api.loading && !api.data ? <Loading label="Loading deals…" /> : null}
        {api.error ? <div className="p-4"><ErrorNote message={api.error} onRetry={api.reload} /></div> : null}
        {api.data ? (
          <DataTable
            rows={api.data.items}
            empty="No deals match these filters."
            columns={[
              {
                key: "name",
                header: "Opportunity",
                sort: (row) => row.name,
                render: (row) => (
                  <div>
                    <Link to={`/deals/${row.id}?${params.toString()}`} className="font-medium text-accent hover:underline">
                      {row.name}
                    </Link>
                    <div className="text-xs text-muted">{row.customer_name}</div>
                  </div>
                ),
              },
              { key: "stage", header: "Stage", sort: (row) => row.stage, render: (row) => row.stage },
              { key: "value", header: "Value", sort: (row) => row.value, className: "text-right tabular-nums", render: (row) => gbp(row.value) },
              { key: "close", header: "Close", sort: (row) => row.close_date, render: (row) => formatDate(row.close_date) },
              { key: "health", header: "Health", sort: (row) => row.health, render: (row) => <StatusPill value={row.health} /> },
              { key: "momentum", header: "Momentum", sort: (row) => row.momentum, render: (row) => <StatusPill value={row.momentum} /> },
              { key: "crm", header: "CRM %", sort: (row) => row.crm_probability, className: "tabular-nums", render: (row) => `${row.crm_probability}%` },
              {
                key: "sim",
                header: "Simulated %",
                sort: (row) => row.simulated_probability ?? -1,
                className: "tabular-nums",
                render: (row) => (row.simulated_probability === null ? "—" : `${row.simulated_probability}%`),
              },
              { key: "owner", header: "Owner", sort: (row) => row.owner_name, render: (row) => row.owner_name },
              { key: "next", header: "Next step", render: (row) => <span className="block max-w-xs text-muted">{row.next_step}</span> },
            ]}
          />
        ) : null}
      </Card>
    </div>
  );
}
