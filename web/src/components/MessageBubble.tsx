import { motion } from "motion/react";

import { riseIn } from "../motion";
import type { MessageDto, MessagePartDto } from "../api/types";
import type { ThinkingDraft } from "../features/sessions/sessionReducer";
import { Markdown } from "./markdown";
import { ThinkingDisclosure } from "./ThinkingDisclosure";

/** The assistant speaks under the product's name; the user's cards need none. */
const ASSISTANT_LABEL = "Make Code Great Again";

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
      <motion.article
        className="message-bubble message-assistant"
        data-transient
        variants={riseIn}
        initial="initial"
        animate="animate"
      >
        <header>{ASSISTANT_LABEL} · 生成中</header>
        {hasThinking ? (
          <ThinkingDisclosure
            text={thinking.text}
            live
            closed={thinking.closed}
          />
        ) : null}
        {hasText ? <p>{text}</p> : null}
      </motion.article>
    );
  }

  const segments = renderableSegments(message.parts);
  if (segments.length === 0) return null;

  // Only committed assistant text goes through the Markdown subset: user input
  // stays literal, and the transient streaming draft stays plain until commit so
  // half-streamed markers never flicker between raw and rendered.
  const renderText = (text: string, index: number) =>
    message.role === "assistant" ? (
      <Markdown key={index} text={text} />
    ) : (
      <p key={index}>{text}</p>
    );

  return (
    <motion.article
      className={`message-bubble message-${message.role}`}
      variants={riseIn}
      initial="initial"
      animate="animate"
    >
      {message.role === "assistant" ? <header>{ASSISTANT_LABEL}</header> : null}
      {segments.map((segment, index) =>
        segment.kind === "thinking" ? (
          <ThinkingDisclosure key={index} text={segment.text} />
        ) : (
          renderText(segment.text, index)
        ),
      )}
    </motion.article>
  );
}
