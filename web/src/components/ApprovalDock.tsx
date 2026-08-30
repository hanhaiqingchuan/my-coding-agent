import { motion } from "motion/react";

import { gateRise } from "../motion";
import type { ApprovalDecision, PendingApprovalDto } from "../api/types";
import { CommandDetails, stringValue } from "./ToolCard";

type ApprovalDockProps = {
  pendingApproval: PendingApprovalDto | null;
  onResolve(toolCallId: string, decision: ApprovalDecision): void;
};

export function ApprovalDock({
  pendingApproval,
  onResolve,
}: ApprovalDockProps) {
  if (pendingApproval === null) return null;
  const isCommand =
    pendingApproval.name.includes("command") ||
    stringValue(pendingApproval.input.command) !== null;
  const isWrite = pendingApproval.name.includes("write");

  return (
    <motion.section
      key={pendingApproval.tool_call_id}
      className="approval-dock"
      aria-label="待审批"
      variants={gateRise}
      initial="initial"
      animate="animate"
    >
      <header>
        <p>需要审批</p>
        <h2>{pendingApproval.name}</h2>
      </header>
      {isWrite && pendingApproval.preview !== null ? (
        <pre className="tool-diff">{pendingApproval.preview}</pre>
      ) : null}
      {isCommand ? (
        <CommandDetails
          input={pendingApproval.input}
          metadata={pendingApproval.metadata}
        />
      ) : null}
      {!isCommand && !isWrite ? (
        <pre>{JSON.stringify(pendingApproval.input, null, 2)}</pre>
      ) : null}
      <div className="approval-actions">
        <button
          type="button"
          className="approval-reject"
          onClick={() => onResolve(pendingApproval.tool_call_id, "reject")}
        >
          拒绝
        </button>
        <button
          type="button"
          className="approval-approve"
          onClick={() => onResolve(pendingApproval.tool_call_id, "approve")}
        >
          批准
        </button>
      </div>
    </motion.section>
  );
}
