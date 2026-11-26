import { useSearchParams } from "react-router-dom";

export const FILTER_KEYS = [
  "date_from",
  "date_to",
  "owner_id",
  "pipeline",
  "region",
  "business_unit",
  "product",
  "customer_id",
  "opportunity_id",
  "stage",
] as const;

export type FilterKey = (typeof FILTER_KEYS)[number];
export type Filters = Record<FilterKey, string>;

export function useFilters() {
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries(FILTER_KEYS.map((key) => [key, params.get(key) ?? ""])) as Filters;
  const query = FILTER_KEYS.filter((key) => filters[key])
    .map((key) => `${encodeURIComponent(key)}=${encodeURIComponent(filters[key])}`)
    .join("&");
  const active = FILTER_KEYS.some((key) => filters[key]);

  function setFilter(key: FilterKey, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  function setMany(values: Partial<Filters>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  }

  function clear() {
    const next = new URLSearchParams(params);
    for (const key of FILTER_KEYS) next.delete(key);
    setParams(next, { replace: true });
  }

  return { filters, query, active, params, setParams, setFilter, setMany, clear };
}
