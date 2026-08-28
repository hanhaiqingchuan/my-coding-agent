import { useState, type ReactNode } from "react";

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
  const setDrawer = (isOpen: boolean) => {
    setDetailsOpen(isOpen);
    onDetailsDrawerChange?.(isOpen);
  };

  return (
    <div className="app-shell">
      <nav className="session-sidebar" aria-label="Sessions and workspace">
        {sidebar}
      </nav>
      <main className="conversation-panel" aria-label="Conversation">
        <button
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
        <div id="run-details-drawer" className="run-details-drawer" role="dialog" aria-label="Run details" aria-modal="true">
          <button type="button" className="drawer-close" onClick={() => setDrawer(false)}>
            Close run details
          </button>
          {runDetails}
        </div>
      ) : null}
    </div>
  );
}
