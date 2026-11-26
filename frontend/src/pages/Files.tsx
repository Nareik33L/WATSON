import { useState } from "react";
import { Card, ErrorNote, Loading, Notice, PageHeader } from "@/components/ui";
import { formatWhen } from "@/lib/format";
import { useApi } from "@/lib/useApi";

interface StoredFile {
  id: string;
  name: string;
  kind: string;
  row_count: number;
  issue_count: number;
  summary: string;
  findings: string[];
  uploaded_at: string | null;
}

export function FilesPage() {
  const api = useApi<{ note: string; items: StoredFile[] }>("/api/files");
  const [selected, setSelected] = useState<string | null>(null);
  if (api.loading && !api.data) return <Loading label="Loading files…" />;
  if (api.error || !api.data) return <ErrorNote message={api.error ?? "Files could not be loaded."} onRetry={api.reload} />;
  const current = api.data.items.find((item) => item.id === selected) ?? api.data.items[0];
  const workbooks = api.data.items.filter((item) => item.kind === "workbook");
  const reports = api.data.items.filter((item) => item.kind === "report");
  return (
    <div>
      <PageHeader title="Files & reports" description={api.data.note} />
      <div className="mb-4 rounded-xl border border-dashed border-line bg-white px-4 py-6 text-sm text-muted">
        Workbook upload is switched off. Dropping a file here would not inspect it, and an original workbook is never overwritten.
      </div>
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          <Card>
            <h2 className="text-sm font-semibold">Workbooks</h2>
            <ul className="mt-3 divide-y divide-line">
              {workbooks.map((file) => (
                <li key={file.id}>
                  <button type="button" className="w-full py-3 text-left" onClick={() => setSelected(file.id)}>
                    <div className="text-sm font-medium">{file.name}</div>
                    <div className="text-xs text-muted">{file.row_count} rows · {file.issue_count} findings · {formatWhen(file.uploaded_at)}</div>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
          <Card>
            <h2 className="text-sm font-semibold">Reports</h2>
            <ul className="mt-3 space-y-3">
              {reports.map((file) => (
                <li key={file.id}>
                  <div className="text-sm font-medium">{file.name}</div>
                  <p className="text-sm text-muted">{file.summary}</p>
                  <p className="mt-1 text-xs text-muted">Export is not available in this prototype.</p>
                </li>
              ))}
            </ul>
          </Card>
        </div>
        {current ? (
          <Card>
            <h2 className="text-sm font-semibold">{current.name}</h2>
            <p className="mt-2 text-sm text-muted">{current.summary}</p>
            <ul className="mt-3 list-disc space-y-2 pl-4 text-sm">
              {current.findings.map((finding) => (
                <li key={finding}>{finding}</li>
              ))}
            </ul>
            <div className="mt-3"><Notice>These findings are fictional. No workbook was parsed.</Notice></div>
          </Card>
        ) : null}
      </div>
    </div>
  );
}
