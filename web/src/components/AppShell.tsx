import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from "react";

type AppShellProps = {
  sidebar: ReactNode;
  conversation: ReactNode;
  runDetails: ReactNode;
  onDetailsDrawerChange?(isOpen: boolean): void;
};

/** Collapsed rails keep just enough width for their expand button. */
const COLLAPSED_TRACK = "56px";
const SIDEBAR_MIN = 200;
const SIDEBAR_MAX = 420;
const DETAILS_MIN = 240;
const DETAILS_MAX = 560;

const clampWidth = (width: number, min: number, max: number) =>
  Math.min(max, Math.max(min, width));

export function AppShell({
  sidebar,
  conversation,
  runDetails,
  onDetailsDrawerChange,
}: AppShellProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [detailsCollapsed, setDetailsCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(264);
  const [detailsWidth, setDetailsWidth] = useState(320);
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

  // Dragging the left rail's right edge rightwards widens it; dragging the right
  // rail's left edge rightwards narrows it, hence the opposite signs.
  const resizeSidebar = useCallback((delta: number) => {
    setSidebarWidth((width) => clampWidth(width + delta, SIDEBAR_MIN, SIDEBAR_MAX));
  }, []);
  const resizeDetails = useCallback((delta: number) => {
    setDetailsWidth((width) => clampWidth(width - delta, DETAILS_MIN, DETAILS_MAX));
  }, []);

  const shellStyle = {
    "--sidebar-track": sidebarCollapsed
      ? COLLAPSED_TRACK
      : `${sidebarWidth}px`,
    "--details-track": detailsCollapsed ? COLLAPSED_TRACK : `${detailsWidth}px`,
  } as CSSProperties;

  return (
    <div className="app-shell" style={shellStyle}>
      <nav
        className="session-sidebar"
        aria-label="会话与工作区"
        data-collapsed={sidebarCollapsed}
      >
        <button
          type="button"
          className="rail-toggle"
          aria-expanded={!sidebarCollapsed}
          onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
        >
          {sidebarCollapsed ? "»" : "«"}
        </button>
        {sidebarCollapsed ? null : (
          <>
            <RailResizeHandle label="调整会话栏宽度" onDelta={resizeSidebar} />
            {sidebar}
          </>
        )}
      </nav>
      <main className="conversation-panel" aria-label="对话">
        <button
          ref={triggerRef}
          className="run-details-toggle"
          type="button"
          aria-expanded={detailsOpen}
          aria-controls="run-details-drawer"
          onClick={() => setDrawer(true)}
        >
          打开运行详情
        </button>
        {conversation}
      </main>
      <aside
        className="run-details-panel"
        aria-label="运行详情"
        data-collapsed={detailsCollapsed}
      >
        <button
          type="button"
          className="rail-toggle"
          aria-expanded={!detailsCollapsed}
          onClick={() => setDetailsCollapsed((collapsed) => !collapsed)}
        >
          {detailsCollapsed ? "«" : "»"}
        </button>
        {detailsCollapsed ? null : (
          <>
            <RailResizeHandle label="调整运行详情栏宽度" onDelta={resizeDetails} />
            {runDetails}
          </>
        )}
      </aside>
      {detailsOpen ? (
        <div
          ref={drawerRef}
          id="run-details-drawer"
          className="run-details-drawer"
          role="dialog"
          aria-label="运行详情"
          aria-modal="true"
          onKeyDown={handleDrawerKeyDown}
        >
          <button
            type="button"
            className="drawer-close"
            onClick={() => setDrawer(false)}
          >
            关闭运行详情
          </button>
          {runDetails}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The draggable inner edge of a rail: mouse drag reports the horizontal delta, and
 * the arrow keys step 16px for keyboard users. Focus lives on the separator itself,
 * which WAI-ARIA types as a vertical divider between regions.
 */
function RailResizeHandle({
  label,
  onDelta,
}: {
  label: string;
  onDelta(deltaPx: number): void;
}) {
  const [dragging, setDragging] = useState(false);
  const lastX = useRef(0);
  const onDeltaRef = useRef(onDelta);
  onDeltaRef.current = onDelta;

  useEffect(() => {
    if (!dragging) return undefined;
    const move = (event: MouseEvent) => {
      onDeltaRef.current(event.clientX - lastX.current);
      lastX.current = event.clientX;
    };
    const stop = () => setDragging(false);
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", stop);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", stop);
    };
  }, [dragging]);

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      tabIndex={0}
      className={`rail-resize${dragging ? " rail-resize-dragging" : ""}`}
      onMouseDown={(event) => {
        lastX.current = event.clientX;
        setDragging(true);
      }}
      onKeyDown={(event) => {
        if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
          event.preventDefault();
          onDeltaRef.current(event.key === "ArrowRight" ? 16 : -16);
        }
      }}
    />
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
