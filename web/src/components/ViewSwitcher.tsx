export type AppView = "sessions" | "evaluations";

type ViewSwitcherProps = {
  view: AppView;
  onViewChange(view: AppView): void;
};

/**
 * The left rail's top-level navigation. The app keeps one page-level state
 * instead of a router: both views are local, and no new dependency is needed.
 */
export function ViewSwitcher({ view, onViewChange }: ViewSwitcherProps) {
  return (
    <nav className="view-switch" aria-label="Views">
      <button
        type="button"
        className="view-tab"
        aria-current={view === "sessions" ? "page" : undefined}
        onClick={() => onViewChange("sessions")}
      >
        Sessions
      </button>
      <button
        type="button"
        className="view-tab"
        aria-current={view === "evaluations" ? "page" : undefined}
        onClick={() => onViewChange("evaluations")}
      >
        Evaluations
      </button>
    </nav>
  );
}
