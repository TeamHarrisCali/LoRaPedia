import { useState } from "react";
import { api, errorText } from "../lib/api";
import { useToast } from "../lib/toast";
import { Button } from "./ui";

/** Delete one question. Asks once more in place (no dialog) because it cannot be undone. */
export function DeleteQuestion({ id, onDeleted, compact = false }: { id: number; onDeleted?: () => void; compact?: boolean }) {
  const toast = useToast();
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState(false);
  async function remove() {
    setBusy(true);
    try { await api.del(`/api/queries/${id}`); toast.success("Question deleted."); onDeleted?.(); }
    catch (e) { toast.error(errorText(e)); setBusy(false); setSure(false); }
  }
  if (!sure) return <Button variant="ghost" icon="trash" aria-label="Delete this question" title="Delete this question" onClick={() => setSure(true)}>{compact ? null : "Delete"}</Button>;
  return (
    <span className="inline-flex items-center gap-1 text-sm" role="group" aria-label="Confirm delete">
      <span className="text-muted">Delete it?</span>
      <Button variant="danger" busy={busy} onClick={() => void remove()}>Yes</Button>
      <Button variant="ghost" onClick={() => setSure(false)}>No</Button>
    </span>
  );
}
