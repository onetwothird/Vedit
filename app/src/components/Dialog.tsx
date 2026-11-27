import { useEffect, useId, useRef, type ReactNode } from "react";

/** A modal question (the browser's own <dialog>: focus stays inside, Escape closes it). */
export default function Dialog({ title, children, onClose }: { title: string; children: ReactNode; onClose(): void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const head = useId();
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal?.();
    return () => d?.close?.();
  }, []);
  return (
    <dialog
      ref={ref}
      className="sync-dialog"
      aria-labelledby={head}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <p id={head} className="sync-dialog-title">{title}</p>
      {children}
    </dialog>
  );
}
