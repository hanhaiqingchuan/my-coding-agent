import type { JsonValue, ToolExecutionDto } from "../api/types";

type ToolCardProps = {
  tool: ToolExecutionDto;
  outputDraft?: string;
};

export function ToolCard({ tool, outputDraft }: ToolCardProps) {
  const command = stringValue(tool.input.command);
  const cwd = stringValue(tool.input.cwd);
  const reason = stringValue(tool.input.reason);
  const diff =
    stringValue(tool.input.diff) ??
    stringValue(tool.input.patch) ??
    stringValue(tool.input.content);
  const isCommand = tool.name.includes("command") || command !== null;
  const isWrite = tool.name.includes("write") || diff !== null;

  return (
    <article
      className={`tool-card tool-${tool.execution_state}`}
      aria-label={`${tool.name} ${statusLabel(tool.execution_state)}`}
    >
      <header>
        <strong>{tool.name}</strong>
        <span className="tool-status">{statusLabel(tool.execution_state)}</span>
      </header>
      <details>
        <summary>Details</summary>
        {isCommand ? (
          <CommandDetails
            command={command}
            cwd={cwd}
            reason={reason}
            timeoutSeconds={tool.input.timeout_seconds}
          />
        ) : null}
        {isWrite && diff !== null ? (
          <pre className="tool-diff">{diff}</pre>
        ) : null}
        {!isCommand && !isWrite ? (
          <pre>{JSON.stringify(tool.input, null, 2)}</pre>
        ) : null}
        {tool.result !== null ? (
          <pre
            className={
              tool.result.ok ? "tool-result" : "tool-result tool-result-error"
            }
          >
            {tool.result.content}
          </pre>
        ) : null}
        {outputDraft !== undefined && outputDraft.length > 0 ? (
          <pre className="tool-output-draft">{outputDraft}</pre>
        ) : null}
      </details>
    </article>
  );
}

export function CommandDetails({
  command,
  cwd,
  reason,
  timeoutSeconds,
}: {
  command: string | null;
  cwd: string | null;
  reason: string | null;
  timeoutSeconds: JsonValue | undefined;
}) {
  return (
    <dl className="tool-command-details">
      <div>
        <dt>Command</dt>
        <dd>{command ?? "—"}</dd>
      </div>
      <div>
        <dt>Working directory</dt>
        <dd>{cwd ?? "—"}</dd>
      </div>
      <div>
        <dt>Reason</dt>
        <dd>{reason ?? "—"}</dd>
      </div>
      <div>
        <dt>Timeout</dt>
        <dd>{timeoutLabel(timeoutSeconds)}</dd>
      </div>
      {/* run_command is never a security sandbox: an approved command runs with
          the current operating system user's privileges, so the warning belongs
          to every command card and no backend flag may switch it off. */}
      <div className="tool-risk">
        <dt>Warning</dt>
        <dd>
          This command is not sandboxed and runs with your operating system
          user&apos;s full privileges.
        </dd>
      </div>
    </dl>
  );
}

function timeoutLabel(timeoutSeconds: JsonValue | undefined): string {
  // Only the frozen `timeout_seconds` argument is shown; when the model omitted
  // it the backend default stays invisible rather than being guessed here.
  return typeof timeoutSeconds === "number" ? `${timeoutSeconds}s` : "—";
}

export function statusLabel(
  state: ToolExecutionDto["execution_state"],
): string {
  return state.replaceAll("_", " ");
}

export function stringValue(value: JsonValue | undefined): string | null {
  return typeof value === "string" ? value : null;
}
