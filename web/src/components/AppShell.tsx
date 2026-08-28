import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

type AppShellProps = {
  sidebar: ReactNode;
  conversation: ReactNode;
  runDetails: ReactNode;
  onDetailsDrawerChange?(isOpen: boolean): void;
};

export function AppShell({
  sidebar,
  conversation,
  runDetails,
  onDetailsDrawerChange,
}: AppShellProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);
  const restoreTriggerFocus = useRef(false);
  const setDrawer = (isOpen: boolean) => {
    if (!isOpen) restoreTriggerFocus.current = true;
    setDetailsOpen(isOpen);
    onDetailsDrawerChange?.(isOpen);
  };

  useEffect(() => {
    if (!detailsOpen) {
      if (restoreTriggerFocus.current) {
        triggerRef.current?.focus();
        restoreTriggerFocus.current = false;
      }
      return;
    }
    focusableElements(drawerRef.current)[0]?.focus();
  }, [detailsOpen]);

  const handleDrawerKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      setDrawer(false);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = focusableElements(event.currentTarget);
    const first = focusable[0];
    const last = focusable.at(-1);
    if (first === undefined || last === undefined) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="app-shell">
      <nav className="session-sidebar" aria-label="Sessions and workspace">
        {sidebar}
      </nav>
      <main className="conversation-panel" aria-label="Conversation">
        <button
          ref={triggerRef}
          className="run-details-toggle"
          type="button"
          aria-expanded={detailsOpen}
          aria-controls="run-details-drawer"
          onClick={() => setDrawer(true)}
        >
          Open run details
        </button>
        {conversation}
      </main>
      <aside className="run-details-panel" aria-label="Run details">
        {runDetails}
      </aside>
      {detailsOpen ? (
        <div
          ref={drawerRef}
          id="run-details-drawer"
          className="run-details-drawer"
          role="dialog"
          aria-label="Run details"
          aria-modal="true"
          onKeyDown={handleDrawerKeyDown}
        >
          <button
            type="button"
            className="drawer-close"
            onClick={() => setDrawer(false)}
          >
            Close run details
          </button>
          {runDetails}
        </div>
      ) : null}
    </div>
  );
}

function focusableElements(container: HTMLElement | null): HTMLElement[] {
  if (container === null) return [];
  return Array.from(
    container.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => !element.hasAttribute("hidden"));
}
