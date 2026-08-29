import { useId, useState } from "react";

type ThinkingDisclosureProps = {
  text: string;
  /**
   * A live draft streams inside the transient bubble: it opens while the block
   * arrives and follows the backend's close signal afterwards.
   */
  live?: boolean;
  /** The backend's per-block signal: `true` collapses, a later delta re-opens. */
  closed?: boolean;
  /** What the collapsed pill names; judgement rationales reuse the same control. */
  label?: string;
};

export function ThinkingDisclosure({
  text,
  live = false,
  closed = false,
  label = "思考中",
}: ThinkingDisclosureProps) {
  const [open, setOpen] = useState(live && !closed);
  const [seenClosed, setSeenClosed] = useState(closed);
  if (closed !== seenClosed) {
    // The close signal is the only external driver: a finished block collapses
    // the box, and the next block's first delta expands it again. Manual toggles
    // between signals stay untouched.
    setSeenClosed(closed);
    setOpen(!closed);
  }
  const bodyId = useId();

  return (
    <div
      className={`thinking-box${live ? " thinking-live" : ""}`}
      data-open={open}
    >
      <button
        type="button"
        className="thinking-toggle"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="thinking-chevron" aria-hidden="true" />
        {label} · {text.length} 字
      </button>
      {/* The body stays mounted so the collapse runs as a CSS transition on
          grid-template-rows; the global prefers-reduced-motion rule removes it. */}
      <div className="thinking-body" id={bodyId}>
        <pre className="thinking-text">{text}</pre>
      </div>
    </div>
  );
}
