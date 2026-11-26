import { formatWhen, titleCase } from "@/lib/format";
import type { TimelineItem } from "@/lib/types";
import { SourcePill } from "@/components/ui";

export function Timeline({ items }: { items: TimelineItem[] }) {
  if (items.length === 0) return <p className="text-sm text-muted">No events in this view.</p>;
  return (
    <ol className="space-y-4">
      {items.map((item) => (
        <li key={item.id} className="flex gap-3">
          <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-accent" />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <SourcePill system={item.source_system} label={item.source_label} />
              <span className="text-xs text-muted">{formatWhen(item.occurred_at)}</span>
              <span className="text-xs uppercase tracking-wide text-muted">{titleCase(item.kind)}</span>
              {item.direction ? <span className="text-xs text-muted">{item.direction}</span> : null}
            </div>
            <div className="mt-1 text-sm font-medium text-ink">{item.title}</div>
            <p className="mt-0.5 text-sm text-muted">{item.summary}</p>
            {item.source_reference ? (
              <p className="mt-1 font-mono text-[11px] text-muted">
                {item.source_reference}
                <span className="ml-2 font-sans">Demo reference. There is no live system to open.</span>
              </p>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
