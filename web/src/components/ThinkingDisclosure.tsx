import { useEffect, useId, useRef, useState } from "react";

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
  label,
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
  // "思考中" only while reasoning is still arriving; a closed or committed block
  // reports what it is — finished reasoning.
  const displayLabel = label ?? (live && !closed ? "思考中" : "思考完成");

  // While the block streams open, the newest line stays in view; a reader who
  // scrolls up unpins the follow until they return to the bottom.
  const textRef = useRef<HTMLPreElement>(null);
  const pinnedToBottom = useRef(true);
  useEffect(() => {
    const element = textRef.current;
    if (element !== null && open && pinnedToBottom.current) {
      element.scrollTop = element.scrollHeight;
    }
  }, [text, open]);
  const handleScroll = () => {
    const element = textRef.current;
    if (element === null) return;
    const distanceFromBottom =
      element.scrollHeight - element.scrollTop - element.clientHeight;
    pinnedToBottom.current = distanceFromBottom < 24;
  };

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
        {displayLabel} · {text.length} 字
      </button>
      {/* The body stays mounted so the collapse runs as a CSS transition on
          grid-template-rows; the global prefers-reduced-motion rule removes it. */}
      <div className="thinking-body" id={bodyId}>
        <pre className="thinking-text" ref={textRef} onScroll={handleScroll}>
          {text}
        </pre>
      </div>
    </div>
  );
}
