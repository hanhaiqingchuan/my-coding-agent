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
    <section className="approval-dock" aria-label="Pending approval">
      <header>
        <p>Approval required</p>
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
          Reject
        </button>
        <button
          type="button"
          className="approval-approve"
          onClick={() => onResolve(pendingApproval.tool_call_id, "approve")}
        >
          Approve
        </button>
      </div>
    </section>
  );
}
