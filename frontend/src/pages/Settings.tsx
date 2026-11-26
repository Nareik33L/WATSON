import { useState } from "react";
import { Button, Card, Dialog, ErrorNote, Input, Loading, Notice, PageHeader } from "@/components/ui";
import { sendJson } from "@/lib/api";
import { useApi } from "@/lib/useApi";

interface SettingsPayload {
  user: { name: string; email: string; role: string };
  organisation: { name: string };
  database: string;
  database_file: string;
  encryption: string;
  external_ai: string;
  live_integrations: string;
  poll_interval_minutes: number;
  poll_note: string;
  privacy: string[];
}

export function SettingsPage() {
  const api = useApi<SettingsPayload>("/api/settings");
  const [minutes, setMinutes] = useState<string | null>(null);
  const [banner, setBanner] = useState("");
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);

  async function save() {
    setError("");
    try {
      const result = await sendJson<SettingsPayload>("/api/settings", "PATCH", {
        poll_interval_minutes: Number(minutes ?? api.data?.poll_interval_minutes ?? 15),
      });
      setBanner("Saved locally. Polling is not running.");
      setMinutes(String(result.poll_interval_minutes));
      api.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The setting was not saved.");
    }
  }

  async function reset() {
    await sendJson("/api/demo/reset", "POST");
    window.location.assign("/");
  }

  if (api.loading && !api.data) return <Loading label="Loading settings…" />;
  if (api.error || !api.data) return <ErrorNote message={api.error ?? "Settings could not be loaded."} onRetry={api.reload} />;
  const data = api.data;
  const value = minutes ?? String(data.poll_interval_minutes);
  return (
    <div>
      <PageHeader title="Settings" description="This prototype is a single local workspace. It does not ask you to sign in." />
      {banner ? <div className="mb-3"><Notice tone="good">{banner}</Notice></div> : null}
      {error ? <div className="mb-3"><ErrorNote message={error} /></div> : null}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="text-sm font-semibold">Demo profile</h2>
          <dl className="mt-3 space-y-2 text-sm">
            <div><dt className="text-xs text-muted">Name</dt><dd>{data.user.name}</dd></div>
            <div><dt className="text-xs text-muted">Email</dt><dd>{data.user.email}</dd></div>
            <div><dt className="text-xs text-muted">Role</dt><dd>{data.user.role}</dd></div>
            <div><dt className="text-xs text-muted">Workspace</dt><dd>{data.organisation.name}</dd></div>
          </dl>
        </Card>
        <Card>
          <h2 className="text-sm font-semibold">Local data</h2>
          <dl className="mt-3 space-y-2 text-sm">
            <div><dt className="text-xs text-muted">Database</dt><dd>{data.database} · {data.database_file}</dd></div>
            <div><dt className="text-xs text-muted">Encryption</dt><dd>{data.encryption}</dd></div>
            <div><dt className="text-xs text-muted">External AI</dt><dd>{data.external_ai}</dd></div>
            <div><dt className="text-xs text-muted">Live integrations</dt><dd>{data.live_integrations}</dd></div>
          </dl>
        </Card>
        <Card>
          <h2 className="text-sm font-semibold">Future poll interval</h2>
          <p className="mt-2 text-sm text-muted">{data.poll_note}</p>
          <div className="mt-3 flex items-center gap-2">
            <Input type="number" min={5} max={1440} aria-label="Poll interval minutes" value={value} onChange={(event) => setMinutes(event.target.value)} className="max-w-[8rem]" />
            <span className="text-sm text-muted">minutes</span>
            <Button variant="primary" onClick={() => void save()}>Save</Button>
          </div>
        </Card>
        <Card>
          <h2 className="text-sm font-semibold">Privacy</h2>
          <ul className="mt-3 list-disc space-y-2 pl-4 text-sm text-muted">
            {data.privacy.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </Card>
      </div>
      <Card className="mt-4">
        <h2 className="text-sm font-semibold">Reset demo data</h2>
        <p className="mt-2 text-sm text-muted">Replace the fictional portfolio with a fresh copy. Decisions you made in this prototype are cleared. Live data is not involved.</p>
        <Button className="mt-3" variant="danger" onClick={() => setConfirm(true)}>Reset demo data</Button>
      </Card>
      <Dialog open={confirm} title="Reset the demo portfolio?" onClose={() => setConfirm(false)}>
        <p className="text-sm text-muted">This clears local decisions in the demo database and loads the fictional portfolio again.</p>
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={() => setConfirm(false)}>Cancel</Button>
          <Button variant="danger" onClick={() => void reset()}>Reset</Button>
        </div>
      </Dialog>
    </div>
  );
}
