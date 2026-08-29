import type { MessageDto, MessagePartDto } from "../api/types";
import type { ThinkingDraft } from "../features/sessions/sessionReducer";
import { ThinkingDisclosure } from "./ThinkingDisclosure";

type MessageBubbleProps = {
  message: MessageDto;
  transient?: boolean;
  text?: string;
  thinking?: ThinkingDraft;
};

type RenderableSegment =
  { kind: "text"; text: string } | { kind: "thinking"; text: string };

/** Walk the parts in server order: consecutive text parts share one paragraph,
 * thinking parts become disclosures. Tool parts render as cards next to the
 * bubble, and an empty thinking block has nothing to disclose. */
function renderableSegments(parts: MessagePartDto[]): RenderableSegment[] {
  const segments: RenderableSegment[] = [];
  let paragraph: string[] = [];
  const flushParagraph = () => {
    if (paragraph.length > 0) {
      segments.push({ kind: "text", text: paragraph.join("\n") });
      paragraph = [];
    }
  };
  for (const part of parts) {
    if (part.type === "text") {
      paragraph.push(part.text);
      continue;
    }
    flushParagraph();
    if (part.type === "thinking" && part.text.length > 0) {
      segments.push({ kind: "thinking", text: part.text });
    }
  }
  flushParagraph();
  return segments;
}

export function MessageBubble({
  message,
  transient = false,
  text,
  thinking,
}: MessageBubbleProps) {
  if (transient) {
    const hasThinking = thinking !== undefined && thinking.text.length > 0;
    const hasText = text !== undefined && text.length > 0;
    if (!hasThinking && !hasText) return null;
    return (
      <article className="message-bubble message-assistant" data-transient>
        <header>Assistant · streaming</header>
        {hasThinking ? (
          <ThinkingDisclosure
            text={thinking.text}
            live
            closed={thinking.closed}
          />
        ) : null}
        {hasText ? <p>{text}</p> : null}
      </article>
    );
  }

  const segments = renderableSegments(message.parts);
  if (segments.length === 0) return null;

  return (
    <article className={`message-bubble message-${message.role}`}>
      <header>{message.role}</header>
      {segments.map((segment, index) =>
        segment.kind === "thinking" ? (
          <ThinkingDisclosure key={index} text={segment.text} />
        ) : (
          <p key={index}>{segment.text}</p>
        ),
      )}
    </article>
  );
}
