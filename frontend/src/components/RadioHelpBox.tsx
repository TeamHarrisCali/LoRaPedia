import { useState } from "react";
import { useToast } from "../lib/toast";
import type { RadioHelp } from "../lib/types";
import { Button } from "./ui";

async function copy(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

/** "Radio not connecting? What to try": worked out on the server from the real device files, so it names the actual cause and command. */
export function RadioHelpBox({ help }: { help: RadioHelp }) {
  const toast = useToast();
  const [copied, setCopied] = useState<string | null>(null);

  async function onCopy(text: string) {
    if (await copy(text)) { setCopied(text); setTimeout(() => setCopied(null), 1500); }
    else toast.error("Could not copy. Select the command and copy it by hand.");
  }

  return (
    <details open={help.open} className="mb-4 rounded-lg border border-line bg-bg p-3 text-sm">
      <summary className="cursor-pointer font-medium">
        <span className={help.open ? "text-amber-text" : ""}>{help.title}</span>
        <span className="ml-2 font-normal text-muted">{help.open ? "what to do" : "what to check"}</span>
      </summary>
      <p className="mt-2">{help.why}</p>
      <ol className="ml-5 mt-2 list-decimal space-y-1">{help.steps.map((s) => <li key={s}>{s}</li>)}</ol>
      {help.commands.map((c) => (
        <div key={c.text} className="mt-3">
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="text-muted">{c.label}</span>
            <Button variant="ghost" aria-label={`Copy: ${c.label}`} onClick={() => void onCopy(c.text)}>{copied === c.text ? "Copied" : "Copy"}</Button>
          </div>
          <pre className="overflow-auto rounded-lg border border-line bg-surface p-2 font-mono text-xs">{c.text}</pre>
        </div>
      ))}
      {help.facts && (
        <p className="mt-3 font-mono text-xs text-muted">
          {Object.entries(help.facts).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`).join("  ·  ")}
        </p>
      )}
      <p className="mt-3 text-muted">Run the commands in a terminal on the computer that runs LoRaPedia, then press Retry above.</p>
    </details>
  );
}
