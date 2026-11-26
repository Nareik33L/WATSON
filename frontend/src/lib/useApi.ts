import { useEffect, useState } from "react";
import { getJson } from "./api";

export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!path) return;
    let cancel = false;
    setLoading(true);
    getJson<T>(path)
      .then((value) => {
        if (!cancel) {
          setData(value);
          setError(null);
        }
      })
      .catch((reason: unknown) => {
        if (!cancel) setError(reason instanceof Error ? reason.message : "The request failed.");
      })
      .finally(() => {
        if (!cancel) setLoading(false);
      });
    return () => {
      cancel = true;
    };
  }, [path, tick]);

  return { data, error, loading, reload: () => setTick((value) => value + 1) };
}
