import { useState } from "react";
import { Button, Card, ErrorNote, Loading, Notice, PageHeader, Pill } from "@/components/ui";
import { sendJson } from "@/lib/api";
import { formatWhen } from "@/lib/format";
import { useApi } from "@/lib/useApi";

interface IntegrationCard {
  id: string;
  key: string;
  name: string;
  summary: string;
  last_sync_at: string | null;
  next_sync_at: string | null;
  records_seen: number;
  connected: boolean;
}

interface SyncJob {
  id: string;
  integration: string;
  key: string;
  trigger: string;
  records_processed: number;
  message: string;
  started_at: string | null;
}

interface Payload {
  disclaimer: string;
  poll_interval_minutes: number;
  polling: boolean;
  items: IntegrationCard[];
  jobs: SyncJob[];
}

export function Integrations() {
  const api = useApi<Payload>("/api/integrations");
  const [banner, setBanner] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function refresh(key: string) {
    setBusy(key);
    setError("");
    try {
      const result = await sendJson<{ job: SyncJob }>(`/api/integrations/${key}/refresh`, "POST");
      setBanner(result.job.message);
      api.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Refresh failed.");
    } finally {
      setBusy(null);
    }
  }

  if (api.loading && !api.data) return <Loading label="Loading integrations…" />;
  if (api.error || !api.data) return <ErrorNote message={api.error ?? "Integrations could not be loaded."} onRetry={api.reload} />;
  return (
    <div>
      <PageHeader title="Integrations" description={api.data.disclaimer} />
      {banner ? <div className="mb-3"><Notice tone="good">{banner}</Notice></div> : null}
      {error ? <div className="mb-3"><ErrorNote message={error} /></div> : null}
      <p className="mb-4 text-sm text-muted">
        Polling is not running. The stored interval is {api.data.poll_interval_minutes} minutes and would only matter in a later phase.
      </p>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {api.data.items.map((item) => (
          <Card key={item.id}>
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">{item.name}</h2>
              <Pill tone="warn">Simulated</Pill>
            </div>
            <p className="mt-2 text-sm text-muted">{item.summary}</p>
            <dl className="mt-3 space-y-1 text-xs text-muted">
              <div>Connected: {item.connected ? "yes" : "no"}</div>
              <div>Last simulated run: {formatWhen(item.last_sync_at)}</div>
              <div>Records fetched: {item.records_seen}</div>
            </dl>
            <Button className="mt-3" disabled={busy === item.key} onClick={() => void refresh(item.key)}>
              {busy === item.key ? "Refreshing…" : "Refresh"}
            </Button>
          </Card>
        ))}
      </div>
      <Card className="mt-4">
        <h2 className="text-sm font-semibold">Sync history</h2>
        <ul className="mt-3 divide-y divide-line">
          {api.data.jobs.map((job) => (
            <li key={job.id} className="py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium">{job.integration}</span>
                <span className="text-xs text-muted">{formatWhen(job.started_at)} · {job.trigger} · {job.records_processed} records</span>
              </div>
              <p className="text-sm text-muted">{job.message}</p>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
