import { Fragment } from "react";

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
  // The backend already returns the authoritative order (run start, assistant message,
  // call order). `call_order` only indexes the calls inside one assistant message and
  // restarts at 0 every round, so re-sorting by it would invent an execution order.
  const { grouped, uncommitted } = groupToolsByMessage(messages, tools);
  const toolCard = (tool: ToolExecutionDto) => (
    <ToolCard
      key={tool.tool_call_id}
      tool={tool}
      outputDraft={toolOutputDrafts[tool.tool_call_id]}
    />
  );

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
          <Fragment key={message.id}>
            <MessageBubble message={message} />
            {(grouped.get(message.id) ?? []).map(toolCard)}
          </Fragment>
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
        {/* Tool calls of an assistant message that is still pending_tools: the snapshot
            only carries committed and interrupted messages, so these belong after the
            streaming draft that requested them. */}
        {uncommitted.map(toolCard)}
      </div>
    </section>
  );
}

function groupToolsByMessage(
  messages: MessageDto[],
  tools: ToolExecutionDto[],
): {
  grouped: Map<string, ToolExecutionDto[]>;
  uncommitted: ToolExecutionDto[];
} {
  const messageIds = new Set(messages.map((message) => message.id));
  const grouped = new Map<string, ToolExecutionDto[]>();
  const uncommitted: ToolExecutionDto[] = [];
  for (const tool of tools) {
    if (!messageIds.has(tool.assistant_message_id)) {
      uncommitted.push(tool);
      continue;
    }
    const group = grouped.get(tool.assistant_message_id);
    if (group === undefined) {
      grouped.set(tool.assistant_message_id, [tool]);
    } else {
      group.push(tool);
    }
  }
  return { grouped, uncommitted };
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
