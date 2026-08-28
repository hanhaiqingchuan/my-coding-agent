import type { MessageDto } from "../api/types";

type MessageBubbleProps = {
  message: MessageDto;
  transient?: boolean;
  text?: string;
};

export function MessageBubble({
  message,
  transient = false,
  text,
}: MessageBubbleProps) {
  const content =
    text ??
    message.parts
      .filter((part) => part.type === "text")
      .map((part) => part.text)
      .join("\n");
  if (content.length === 0) return null;

  return (
    <article
      className={`message-bubble message-${message.role}`}
      data-transient={transient || undefined}
    >
      <header>{transient ? "Assistant · streaming" : message.role}</header>
      <p>{content}</p>
    </article>
  );
}
