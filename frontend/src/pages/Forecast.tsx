import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { FilterBar } from "@/components/FilterBar";
import { Card, ErrorNote, Loading, Notice, PageHeader, Pill } from "@/components/ui";
import { withQuery } from "@/lib/api";
import { gbp, gbpCompact, monthLabel } from "@/lib/format";
import { useFilters } from "@/lib/filters";
import { useApi } from "@/lib/useApi";

interface ForecastPayload {
  label: string;
  disclaimer: string;
  totals: {
    pipeline: number;
    crm_weighted: number;
    simulated_weighted: number;
    conservative: number;
    optimistic: number;
    likely_slips: number;
    gap: number;
  };
  won: { count: number; value: number };
  lost: { count: number; value: number };
  by_stage: { stage: string; value: number; crm_weighted: number; simulated: number; count: number }[];
  by_month: { month: string; value: number; crm_weighted: number; simulated: number; count: number }[];
  discrepancies: { id: string; name: string; customer_name: string; crm_probability: number; simulated_probability: number | null; gap: number | null; note: string }[];
  largest_markdowns: { customer_name: string; name: string }[];
}

export function Forecast() {
  const { query } = useFilters();
  const api = useApi<ForecastPayload>(withQuery("/api/forecast", query));
  if (api.loading && !api.data) return <Loading label="Loading the forecast…" />;
  if (api.error || !api.data) return <ErrorNote message={api.error ?? "The forecast could not be loaded."} onRetry={api.reload} />;
  const data = api.data;
  const months = data.by_month.map((item) => ({ ...item, label: monthLabel(item.month) }));
  const names = data.largest_markdowns.map((item) => item.customer_name).join(", ");
  return (
    <div>
      <PageHeader title="Forecast" description="Every figure on this page is fictional demo data. The adjusted series is a simulated placeholder." />
      <div className="mb-4"><Notice>{data.disclaimer} The series is labelled {data.label.toLowerCase()}.</Notice></div>
      <FilterBar />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Scenario label="CRM weighted" value={data.totals.crm_weighted} hint="Value × CRM probability" />
        <Scenario label="Simulated adjusted" value={data.totals.simulated_weighted} hint="Placeholder" simulated />
        <Scenario label="Conservative" value={data.totals.conservative} hint="Commit category, CRM weighted" />
        <Scenario label="Optimistic" value={data.totals.optimistic} hint="Open pipeline, unweighted" />
      </div>
      <p className="mt-3 text-sm text-muted">
        The simulated placeholder is {gbp(data.totals.gap)} {data.totals.gap >= 0 ? "below" : "above"} the CRM weighted total.
        {names ? ` Largest markdowns: ${names}.` : ""} Likely slips, on CRM weight, are {gbpCompact(data.totals.likely_slips)}.
        Recorded wins {gbp(data.won.value)} ({data.won.count}). Recorded losses {gbp(data.lost.value)} ({data.lost.count}).
      </p>
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <h2 className="text-sm font-semibold">Open pipeline by stage</h2>
          <div className="mt-3 h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.by_stage}>
                <CartesianGrid stroke="#e3e8ef" vertical={false} />
                <XAxis dataKey="stage" tick={{ fontSize: 12 }} />
                <YAxis tickFormatter={(value: number) => gbpCompact(value)} width={64} tick={{ fontSize: 12 }} />
                <Tooltip formatter={(value) => gbp(Number(value))} />
                <Bar dataKey="value" name="Unweighted" fill="#1f4e8c" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="text-xs text-muted">Demo data.</p>
        </Card>
        <Card>
          <h2 className="text-sm font-semibold">Weighted by close month</h2>
          <div className="mt-3 h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={months}>
                <CartesianGrid stroke="#e3e8ef" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                <YAxis tickFormatter={(value: number) => gbpCompact(value)} width={64} tick={{ fontSize: 12 }} />
                <Tooltip formatter={(value) => gbp(Number(value))} />
                <Legend />
                <Bar dataKey="crm_weighted" name="CRM weighted" fill="#1f4e8c" radius={[3, 3, 0, 0]} />
                <Bar dataKey="simulated" name="Simulated placeholder" fill="#9bb8de" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="text-xs text-muted">The simulated series is not a forecast to use.</p>
        </Card>
      </div>
      <Card className="mt-4">
        <h2 className="text-sm font-semibold">Where the CRM and the placeholder disagree</h2>
        <div className="mt-3 overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-[11px] uppercase tracking-wide text-muted">
                <th className="py-2 pr-3 font-medium">Deal</th>
                <th className="py-2 pr-3 font-medium">CRM</th>
                <th className="py-2 pr-3 font-medium">Simulated</th>
                <th className="py-2 font-medium">Note</th>
              </tr>
            </thead>
            <tbody>
              {data.discrepancies.map((item) => (
                <tr key={item.id} className="border-b border-line/80">
                  <td className="py-2 pr-3">
                    <Link to={`/deals/${item.id}`} className="font-medium text-accent hover:underline">{item.name}</Link>
                    <div className="text-xs text-muted">{item.customer_name}</div>
                  </td>
                  <td className="py-2 pr-3 tabular-nums">{item.crm_probability}%</td>
                  <td className="py-2 pr-3 tabular-nums">{item.simulated_probability ?? "—"}%</td>
                  <td className="py-2 text-muted">{item.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-muted">Demo data. Close dates in the notes use calendar dates from the fictional portfolio.</p>
      </Card>
    </div>
  );
}

function Scenario({ label, value, hint, simulated }: { label: string; value: number; hint: string; simulated?: boolean }) {
  return (
    <div className="rounded-xl border border-line bg-white p-4 shadow-card">
      <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-muted">
        {label}
        {simulated ? <Pill tone="simulated">Simulated</Pill> : null}
      </div>
      <div className="mt-2 text-2xl font-semibold tabular-nums">{gbpCompact(value)}</div>
      <div className="mt-1 text-xs text-muted">{hint}</div>
    </div>
  );
}
