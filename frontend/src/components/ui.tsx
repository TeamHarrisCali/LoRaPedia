import { ButtonHTMLAttributes, ReactNode } from "react";
import { Icon } from "./icons";

type Variant = "primary" | "secondary" | "danger" | "ghost";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-amber text-amber-on font-semibold hover:brightness-110",
  secondary: "border border-line bg-raised hover:border-muted",
  danger: "border border-danger text-danger hover:bg-danger/10",
  ghost: "text-muted hover:text-ink hover:bg-raised",
};

export function Button({ variant = "secondary", busy, icon, children, className = "", ...rest }:
  { variant?: Variant; busy?: boolean; icon?: string } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button {...rest} disabled={rest.disabled || busy}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm transition disabled:cursor-not-allowed disabled:opacity-50 ${VARIANTS[variant]} ${className}`}>
      {busy ? <Spinner /> : icon ? <Icon name={icon} size={16} /> : null}
      {children}
    </button>
  );
}

export function Spinner() {
  return <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" role="status" aria-label="Working" />;
}

export function Card({ children, className = "", as: Tag = "section" }: { children: ReactNode; className?: string; as?: "section" | "div" | "li" }) {
  return <Tag className={`rounded-xl border border-line bg-surface/90 p-5 ${className}`}>{children}</Tag>;
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-3xl font-semibold">{title}</h1>
        {subtitle && <p className="mt-1 max-w-2xl text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function SectionTitle({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-3">
      <h2 className="text-xl font-semibold">{children}</h2>
      {hint && <p className="mt-0.5 text-sm text-muted">{hint}</p>}
    </div>
  );
}

export const Help = ({ children }: { children: ReactNode }) => <p className="mt-1 text-sm text-muted">{children}</p>;

export function Field({ label, help, children, htmlFor }: { label: string; help?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="mb-4">
      <label htmlFor={htmlFor} className="mb-1 block text-sm font-medium">{label}</label>
      {children}
      {help && <Help>{help}</Help>}
    </div>
  );
}

export function Toggle({ checked, onChange, label, help, id }: { checked: boolean; onChange: (v: boolean) => void; label: string; help?: ReactNode; id?: string }) {
  return (
    <div className="mb-4">
      <label className="flex cursor-pointer items-center gap-3">
        <input id={id} type="checkbox" role="switch" checked={checked} onChange={(e) => onChange(e.target.checked)} className="h-5 w-5" />
        <span className="text-sm font-medium">{label}</span>
      </label>
      {help && <Help>{help}</Help>}
    </div>
  );
}

export function Progress({ value, max = 100, label, tone = "amber" }: { value: number; max?: number; label: string; tone?: "amber" | "teal" }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)} className="h-2.5 w-full overflow-hidden rounded-full bg-line">
      <div className={`h-full rounded-full transition-[width] duration-300 ${tone === "teal" ? "bg-teal" : "bg-amber"}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Badge({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "teal" | "amber" | "danger" }) {
  const t = { muted: "text-muted border-line", teal: "text-teal border-teal/50", amber: "text-amber-text border-amber/50", danger: "text-danger border-danger/50" }[tone];
  return <span className={`inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${t}`}>{children}</span>;
}

/** Status dot. Colour is never the only signal: the label is always available to screen readers. */
export function Dot({ tone, label, className = "" }: { tone: "teal" | "amber" | "danger" | "muted"; label: string; className?: string }) {
  const bg = { teal: "bg-teal", amber: "bg-amber", danger: "bg-danger", muted: "bg-muted" }[tone];
  return <span role="img" aria-label={label} title={label} className={`inline-block h-2.5 w-2.5 rounded-full ${bg} ${className}`} />;
}

export function Check({ done, size = 18 }: { done: boolean; size?: number }) {
  return done
    ? <span className="inline-flex items-center justify-center rounded-full bg-teal text-bg" style={{ width: size, height: size }} aria-label="Done"><Icon name="check" size={size - 6} strokeWidth={3} /></span>
    : <span className="inline-block rounded-full border border-line" style={{ width: size, height: size }} aria-hidden="true" />;
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line p-8 text-center">
      <p className="font-serif text-lg">{title}</p>
      {children && <div className="mt-2 text-sm text-muted">{children}</div>}
    </div>
  );
}
