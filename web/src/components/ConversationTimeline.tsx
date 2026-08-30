import { Fragment, useEffect, useRef } from "react";
import { motion } from "motion/react";

import { riseIn } from "../motion";
import { describeStopOutcome, failureBannerFor } from "../api/errors";
import type {
  InterruptedBannerDto,
  MessageDto,
  RunDto,
  ToolExecutionDto,
} from "../api/types";
import type { ThinkingDraft } from "../features/sessions/sessionReducer";
import { MessageBubble } from "./MessageBubble";
import { RunFailureBanner } from "./RunFailureBanner";
import { ToolCard } from "./ToolCard";

type ConversationTimelineProps = {
  messages: MessageDto[];
  tools: ToolExecutionDto[];
  assistantDrafts: Record<string, string>;
  thinkingDrafts: Record<string, ThinkingDraft>;
  toolOutputDrafts: Record<string, string>;
  interruptedBanner?: InterruptedBannerDto | null;
  /** The last finished run, when the caller wants its failure summarized. */
  lastFinishedRun?: RunDto | null;
  onAcknowledgeRecovery?(): void;
};

export function ConversationTimeline({
  messages,
  tools,
  assistantDrafts,
  thinkingDrafts,
  toolOutputDrafts,
  interruptedBanner = null,
  lastFinishedRun = null,
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
  // Thinking and text of one round share a draft epoch, so they share one bubble:
  // the thinking box rides above the streaming text and never replaces it.
  const draftEpochs = [
    ...Object.keys(thinkingDrafts),
    ...Object.keys(assistantDrafts).filter(
      (epoch) => !(epoch in thinkingDrafts),
    ),
  ];

  // A finished run only earns a banner when it failed: a user stop, a soft
  // limit and a normal completion are facts, not errors.
  const runFailure = failureBannerFor(lastFinishedRun);

  // Auto-scroll follows the newest content only while the reader stays at the
  // bottom; once they scroll up to reread, their position is respected until
  // they return to the bottom.
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToBottom = useRef(true);
  useEffect(() => {
    const element = scrollRef.current;
    if (element !== null && pinnedToBottom.current) {
      element.scrollTop = element.scrollHeight;
    }
  }, [messages, tools, assistantDrafts, thinkingDrafts, toolOutputDrafts]);
  const handleScroll = () => {
    const element = scrollRef.current;
    if (element === null) return;
    const distanceFromBottom =
      element.scrollHeight - element.scrollTop - element.clientHeight;
    pinnedToBottom.current = distanceFromBottom < 24;
  };

  return (
    <section
      className="conversation-timeline"
      aria-label="对话时间线"
    >
      {interruptedBanner !== null ? (
        <InterruptedBanner
          banner={interruptedBanner}
          onAcknowledgeRecovery={onAcknowledgeRecovery}
        />
      ) : null}
      {runFailure !== null ? <RunFailureBanner failure={runFailure} /> : null}
      <div className="timeline-scroll" ref={scrollRef} onScroll={handleScroll}>
        {messages.map((message) => (
          <Fragment key={message.id}>
            <MessageBubble message={message} />
            {(grouped.get(message.id) ?? []).map(toolCard)}
          </Fragment>
        ))}
        {draftEpochs.map((epoch) => (
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
            text={assistantDrafts[epoch]}
            thinking={thinkingDrafts[epoch]}
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
  const outcome = describeStopOutcome(banner.stop_reason);
  return (
    <motion.aside
      className="recovery-banner"
      role="alert"
      variants={riseIn}
      initial="initial"
      animate="animate"
    >
      <p>
        {restart
          ? "上一轮运行因服务重启而中断。"
          : `上一轮运行已中断：${outcome?.title ?? banner.stop_reason}。`}
      </p>
      {banner.requires_recovery_ack ? (
        <button type="button" onClick={onAcknowledgeRecovery}>
          我已检查工作区/进程
        </button>
      ) : (
        <p>可以继续对话。</p>
      )}
    </motion.aside>
  );
}
