export type AppView = "sessions" | "evaluations";

type ViewSwitcherProps = {
  view: AppView;
  onViewChange(view: AppView): void;
};

/**
 * The left rail's top-level navigation. The app derives the current view from
 * the location hash (`#/evaluations`); a tab click asks the app to rewrite the
 * hash, and the hashchange listener re-renders — no router dependency.
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
