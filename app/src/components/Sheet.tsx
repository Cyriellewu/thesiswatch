import { useEffect, type ReactNode } from "react";

/** Apple-Wallet-style bottom sheet. Conclusion → change → interpretation → evidence. */
export function Sheet({
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
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center sm:justify-center">
      <button
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-black/35 animate-fade-in"
      />
      <div
        role="dialog"
        aria-modal="true"
        className="relative w-full sm:max-w-[480px] bg-surface rounded-t-sheet sm:rounded-sheet shadow-card animate-sheet-in max-h-[88%] sm:max-h-[86vh] flex flex-col"
      >
        <div className="pt-2.5 flex justify-center sm:hidden">
          <div className="h-1.5 w-10 rounded-chip" style={{ background: "var(--hairline)" }} />
        </div>
        <div className="flex items-center justify-between px-5 pt-2 sm:pt-4 pb-3">
          <h2 className="text-[17px] font-bold">{title}</h2>
          <button onClick={onClose} className="text-secondary text-[15px] min-h-[44px] px-2">
            Done
          </button>
        </div>
        <div className="px-5 pb-safe sm:pb-5 overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}
