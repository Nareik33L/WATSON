import { Link } from "react-router-dom";
import { DataTable } from "@/components/DataTable";
import { FilterBar } from "@/components/FilterBar";
import { Card, ErrorNote, Loading, PageHeader, StatusPill } from "@/components/ui";
import { withQuery } from "@/lib/api";
import { formatWhen, gbp } from "@/lib/format";
import { useFilters } from "@/lib/filters";
import { useApi } from "@/lib/useApi";

interface CustomerRow {
  id: string;
  name: string;
  industry: string;
  region: string;
  business_unit: string;
  importance: string;
  owner_name: string;
  open_count: number;
  open_pipeline: number;
  health: string | null;
  last_interaction_at: string | null;
}

export function Customers() {
  const { query, params } = useFilters();
  const api = useApi<{ items: CustomerRow[] }>(withQuery("/api/customers", query));
  return (
    <div>
      <PageHeader title="Customers" description="Accounts in the demo book. Open a name for the people, deals, and timeline." />
      <FilterBar />
      <Card className="p-0">
        {api.loading && !api.data ? <Loading label="Loading customers…" /> : null}
        {api.error ? <div className="p-4"><ErrorNote message={api.error} onRetry={api.reload} /></div> : null}
        {api.data ? (
          <DataTable
            rows={api.data.items}
            empty="No customers match these filters."
            columns={[
              {
                key: "name",
                header: "Customer",
                sort: (row) => row.name,
                render: (row) => (
                  <div>
                    <Link to={`/customers/${row.id}?${params.toString()}`} className="font-medium text-accent hover:underline" data-testid={`customer-${row.id}`}>
                      {row.name}
                    </Link>
                    <div className="text-xs text-muted">{row.industry}</div>
                  </div>
                ),
              },
              { key: "region", header: "Region", sort: (row) => row.region, render: (row) => row.region },
              { key: "unit", header: "Business unit", sort: (row) => row.business_unit, render: (row) => row.business_unit },
              { key: "pipeline", header: "Open pipeline", sort: (row) => row.open_pipeline, className: "tabular-nums", render: (row) => gbp(row.open_pipeline) },
              { key: "health", header: "Health", render: (row) => (row.health ? <StatusPill value={row.health} /> : "—") },
              { key: "last", header: "Last activity", sort: (row) => row.last_interaction_at ?? "", render: (row) => formatWhen(row.last_interaction_at) },
              { key: "owner", header: "Owner", sort: (row) => row.owner_name, render: (row) => row.owner_name },
            ]}
          />
        ) : null}
      </Card>
    </div>
  );
}
