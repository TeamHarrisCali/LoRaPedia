import { Button } from "./ui";

/** Sticky bar that appears only when there are unsaved changes. */
export function SaveBar({ dirty, onSave, onDiscard }: { dirty: boolean; onSave: () => void; onDiscard: () => void }) {
  if (!dirty) return null;
  return (
    <div role="region" aria-label="Unsaved changes" className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-raised/95 px-4 py-3 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-3">
        <p className="text-sm">You have unsaved changes.</p>
        <div className="flex gap-2"><Button variant="ghost" onClick={onDiscard}>Discard</Button><Button variant="primary" onClick={onSave}>Save changes</Button></div>
      </div>
    </div>
  );
}
