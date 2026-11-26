import { addDays } from "@/lib/format";
import { useFilters, type FilterKey } from "@/lib/filters";
import type { FilterOptions } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { useWorkspace } from "@/components/layout/AppShell";
import { Button, Select } from "@/components/ui";

const PRESETS = [
  { id: "any", label: "Any close date" },
  { id: "30", label: "Next 30 days" },
  { id: "90", label: "Next 90 days" },
  { id: "quarter", label: "This quarter" },
];

export function FilterBar() {
  const { filters, active, setFilter, setMany, clear } = useFilters();
  const options = useApi<FilterOptions>("/api/filters");
  const { meta } = useWorkspace();
  const data = options.data;

  function applyPreset(id: string) {
    if (!meta || id === "any") {
      setMany({ date_from: "", date_to: "" });
      return;
    }
    if (id === "30" || id === "90") {
      setMany({ date_from: meta.today, date_to: addDays(meta.today, Number(id)) });
      return;
    }
    const [year, month] = meta.today.split("-").map(Number);
    const quarter = Math.floor(((month ?? 1) - 1) / 3);
    const startMonth = String(quarter * 3 + 1).padStart(2, "0");
    const end = new Date(year, quarter * 3 + 3, 0);
    const endMonth = String(end.getMonth() + 1).padStart(2, "0");
    const endDay = String(end.getDate()).padStart(2, "0");
    setMany({ date_from: `${year}-${startMonth}-01`, date_to: `${end.getFullYear()}-${endMonth}-${endDay}` });
  }

  return (
    <div className="mb-4 rounded-xl border border-line bg-white p-3 shadow-card">
      <div className="flex flex-wrap items-center gap-2">
        {PRESETS.map((preset) => (
          <button
            key={preset.id}
            type="button"
            onClick={() => applyPreset(preset.id)}
            className="rounded-full bg-canvas px-2.5 py-1 text-xs font-medium text-muted hover:text-ink"
          >
            {preset.label}
          </button>
        ))}
        <span className="text-xs text-muted">Date range applies to close dates and commitment due dates.</span>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-5">
        <input
          type="date"
          name="date_from"
          aria-label="Close or due from"
          value={filters.date_from}
          onChange={(event) => setFilter("date_from", event.target.value)}
          className="h-9 rounded-md border border-line px-2 text-sm"
        />
        <input
          type="date"
          name="date_to"
          aria-label="Close or due to"
          value={filters.date_to}
          onChange={(event) => setFilter("date_to", event.target.value)}
          className="h-9 rounded-md border border-line px-2 text-sm"
        />
        <Choice label="Owner" filter="owner_id" value={filters.owner_id} onChange={setFilter} options={data?.owners.map((item) => ({ value: item.id, label: item.name })) ?? []} />
        <Choice label="Pipeline" filter="pipeline" value={filters.pipeline} onChange={setFilter} options={(data?.pipelines ?? []).map(option)} />
        <Choice label="Region" filter="region" value={filters.region} onChange={setFilter} options={(data?.regions ?? []).map(option)} />
        <Choice label="Business unit" filter="business_unit" value={filters.business_unit} onChange={setFilter} options={(data?.business_units ?? []).map(option)} />
        <Choice label="Product" filter="product" value={filters.product} onChange={setFilter} options={(data?.products ?? []).map(option)} />
        <Choice label="Customer" filter="customer_id" value={filters.customer_id} onChange={setFilter} options={data?.customers.map((item) => ({ value: item.id, label: item.name })) ?? []} />
        <Choice label="Opportunity" filter="opportunity_id" value={filters.opportunity_id} onChange={setFilter} options={data?.opportunities.map((item) => ({ value: item.id, label: item.name })) ?? []} />
        <Choice label="Deal stage" testId="filter-stage" filter="stage" value={filters.stage} onChange={setFilter} options={(data?.stages ?? []).map(option)} />
      </div>
      {active ? (
        <div className="mt-3">
          <Button variant="ghost" onClick={clear}>
            Clear filters
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function option(value: string) {
  return { value, label: value };
}

function Choice({
  label,
  filter,
  value,
  options,
  onChange,
  testId,
}: {
  label: string;
  filter: FilterKey;
  value: string;
  options: { value: string; label: string }[];
  onChange: (key: FilterKey, value: string) => void;
  testId?: string;
}) {
  return (
    <Select name={filter} aria-label={label} data-testid={testId} value={value} onChange={(event) => onChange(filter, event.target.value)}>
      <option value="">{label}</option>
      {options.map((item) => (
        <option key={item.value} value={item.value}>
          {item.label}
        </option>
      ))}
    </Select>
  );
}
