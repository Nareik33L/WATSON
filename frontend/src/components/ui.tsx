import { type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes, useEffect } from "react";
import { cn } from "@/lib/utils";

const tones = {
  neutral: "bg-slate-50 text-slate-700 ring-slate-200",
  good: "bg-emerald-50 text-good ring-emerald-200",
  warn: "bg-amber-50 text-warn ring-amber-200",
  bad: "bg-red-50 text-bad ring-red-200",
  info: "bg-accent-soft text-accent ring-blue-200",
  simulated: "bg-white text-accent ring-dashed ring-accent/40",
};

export type Tone = keyof typeof tones;

export function Pill({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", tones[tone])}>
      {children}
    </span>
  );
}

const healthTone: Record<string, Tone> = {
  healthy: "good",
  watch: "warn",
  at_risk: "bad",
  stalled: "bad",
  improving: "good",
  steady: "neutral",
  deteriorating: "bad",
  unknown: "neutral",
  New: "info",
  Upcoming: "info",
  "Due today": "warn",
  Overdue: "bad",
  Completed: "good",
  Cancelled: "neutral",
  Unclear: "warn",
  pending: "warn",
  approved: "good",
  rejected: "bad",
  deferred: "neutral",
};

export function statusTone(value: string): Tone {
  return healthTone[value] ?? "neutral";
}

export function StatusPill({ value, label }: { value: string; label?: string }) {
  const text = label ?? value.replaceAll("_", " ");
  return <Pill tone={statusTone(value)}>{text}</Pill>;
}

const sourceTone: Record<string, string> = {
  outlook: "bg-blue-50 text-blue-800 ring-blue-200",
  gong: "bg-slate-100 text-slate-800 ring-slate-300",
  hubspot: "bg-orange-50 text-orange-900 ring-orange-200",
  excel: "bg-emerald-50 text-emerald-900 ring-emerald-200",
  calendly: "bg-teal-50 text-teal-900 ring-teal-200",
  manual: "bg-stone-50 text-stone-700 ring-stone-200",
};

export function SourcePill({ system, label }: { system: string; label: string }) {
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", sourceTone[system] ?? sourceTone.manual)}>
      {label}
    </span>
  );
}

export function Button({
  variant = "secondary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  const styles = {
    primary: "bg-navy-700 text-white hover:bg-navy-800",
    secondary: "bg-white text-ink ring-1 ring-line hover:bg-slate-50",
    danger: "bg-white text-bad ring-1 ring-red-200 hover:bg-red-50",
    ghost: "text-ink hover:bg-slate-100",
  };
  return (
    <button
      {...props}
      className={cn(
        "inline-flex h-9 items-center justify-center gap-2 rounded-md px-3 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        styles[variant],
        className,
      )}
    />
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cn("rounded-xl border border-line bg-white p-4 shadow-card", className)}>{children}</section>;
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cn(
        "h-9 w-full rounded-md border border-line bg-white px-3 text-sm text-ink shadow-sm",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        props.className,
      )}
    />
  );
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cn(
        "h-9 rounded-md border border-line bg-white px-2 text-sm text-ink shadow-sm",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        props.className,
      )}
    />
  );
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cn(
        "w-full rounded-md border border-line bg-white px-3 py-2 text-sm text-ink shadow-sm",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        props.className,
      )}
    />
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description ? <p className="mt-1 max-w-3xl text-sm text-muted">{description}</p> : null}
      </div>
      {actions}
    </div>
  );
}

export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" | "good" }) {
  const styles = {
    info: "border-blue-200 bg-accent-soft text-navy-800",
    warn: "border-amber-200 bg-amber-50 text-amber-950",
    good: "border-emerald-200 bg-emerald-50 text-emerald-950",
  };
  return <div className={cn("rounded-lg border px-3 py-2 text-sm", styles[tone])}>{children}</div>;
}

export function Loading({ label }: { label: string }) {
  return <p className="py-10 text-sm text-muted">{label}</p>;
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-sm text-bad">
      <p>{message}</p>
      {onRetry ? (
        <Button className="mt-3" variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="px-1 py-8 text-center text-sm text-muted">{children}</p>;
}

export function Dialog({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center p-4 sm:items-center">
      <button type="button" className="absolute inset-0 bg-navy-950/40" aria-label="Close dialog" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label={title} className="relative w-full max-w-lg rounded-xl border border-line bg-white p-5 shadow-card">
        <h2 className="text-base font-semibold text-ink">{title}</h2>
        <div className="mt-3">{children}</div>
      </div>
    </div>
  );
}
