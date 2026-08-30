/**
 * Hand-rolled inline SVG icon set (stroke style, currentColor). No icon font,
 * no emoji: every glyph is decorative (`aria-hidden`) and the control keeps its
 * text label, per the pre-delivery checklist.
 */
type IconProps = { size?: number };

function base(size: number) {
  return {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true as const,
    focusable: false as const,
  };
}

export function IconSend({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M12 19V5" />
      <path d="m5 12 7-7 7 7" />
    </svg>
  );
}

export function IconStop({ size = 13 }: IconProps) {
  return (
    <svg {...base(size)}>
      <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconX({ size = 12 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

export function IconTerminal({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="m5 7 5 5-5 5" />
      <path d="M12 19h7" />
    </svg>
  );
}

export function IconFile({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}

export function IconFileEdit({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
    </svg>
  );
}

export function IconBook({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z" />
      <path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5" />
    </svg>
  );
}

export function IconKey({ size = 13 }: IconProps) {
  return (
    <svg {...base(size)}>
      <circle cx="8" cy="12" r="4" />
      <path d="M12 12h9" />
      <path d="M17 12v4" />
      <path d="M21 12v3" />
    </svg>
  );
}

export function IconChevronLeft({ size = 12 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="m14 6-6 6 6 6" />
    </svg>
  );
}

export function IconChevronRight({ size = 12 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="m10 6 6 6-6 6" />
    </svg>
  );
}

/** The tool-type glyph riding beside a tool card's name. */
export function ToolGlyph({ name, size = 14 }: { name: string; size?: number }) {
  if (name.includes("command")) return <IconTerminal size={size} />;
  if (name.includes("write")) return <IconFileEdit size={size} />;
  if (name.includes("skill")) return <IconBook size={size} />;
  return <IconFile size={size} />;
}
