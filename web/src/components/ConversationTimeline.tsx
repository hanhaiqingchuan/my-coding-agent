import type {
  InterruptedBannerDto,
  MessageDto,
  ToolExecutionDto,
} from "../api/types";
import { MessageBubble } from "./MessageBubble";
import { ToolCard } from "./ToolCard";

type ConversationTimelineProps = {
  messages: MessageDto[];
  tools: ToolExecutionDto[];
  assistantDrafts: Record<string, string>;
  toolOutputDrafts: Record<string, string>;
  interruptedBanner?: InterruptedBannerDto | null;
  onAcknowledgeRecovery?(): void;
};

export function ConversationTimeline({
  messages,
  tools,
  assistantDrafts,
  toolOutputDrafts,
  interruptedBanner = null,
  onAcknowledgeRecovery,
}: ConversationTimelineProps) {
  return (
    <section
      className="conversation-timeline"
      aria-label="Conversation timeline"
    >
      {interruptedBanner !== null ? (
        <InterruptedBanner
          banner={interruptedBanner}
          onAcknowledgeRecovery={onAcknowledgeRecovery}
        />
      ) : null}
      <div className="timeline-scroll">
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
        {Object.entries(assistantDrafts).map(([epoch, text]) => (
          <MessageBubble
            key={epoch}
            message={{
              id: epoch,
              session_id: "",
              run_id: null,
              seq: 0,
              role: "assistant",
              parts: [],
              status: "pending_tools",
              tool_call_id: null,
            }}
            text={text}
            transient
          />
        ))}
        {[...tools]
          .sort((left, right) => left.call_order - right.call_order)
          .map((tool) => (
            <ToolCard
              key={tool.tool_call_id}
              tool={tool}
              outputDraft={toolOutputDrafts[tool.tool_call_id]}
            />
          ))}
      </div>
    </section>
  );
}

function InterruptedBanner({
  banner,
  onAcknowledgeRecovery,
}: {
  banner: InterruptedBannerDto;
  onAcknowledgeRecovery?: () => void;
}) {
  const restart = banner.stop_reason === "server_restart";
  return (
    <aside className="recovery-banner" role="alert">
      <p>
        {restart
          ? "上一 run 因服务重启而中断。"
          : `上一 run 已中断：${banner.stop_reason.replaceAll("_", " ")}。`}
      </p>
      {banner.requires_recovery_ack ? (
        <button type="button" onClick={onAcknowledgeRecovery}>
          我已检查 workspace/进程
        </button>
      ) : (
        <p>可以继续对话。</p>
      )}
    </aside>
  );
}
