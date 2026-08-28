import type { JsonValue, ToolExecutionDto } from "../api/types";

type ToolCardProps = {
  tool: ToolExecutionDto;
  outputDraft?: string;
};

export function ToolCard({ tool, outputDraft }: ToolCardProps) {
  const command = stringValue(tool.input.command);
  const cwd = stringValue(tool.input.cwd);
  const reason = stringValue(tool.input.reason);
  const timeout = tool.input.timeout_ms ?? tool.input.timeout;
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
            timeout={timeout}
            unsandboxed={tool.input.sandboxed === false}
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
  timeout,
  unsandboxed = false,
}: {
  command: string | null;
  cwd: string | null;
  reason: string | null;
  timeout: JsonValue | undefined;
  unsandboxed?: boolean;
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
        <dd>{timeout === undefined ? "—" : String(timeout)}</dd>
      </div>
      {unsandboxed ? (
        <div className="tool-risk">
          <dt>Warning</dt>
          <dd>This command is not sandboxed</dd>
        </div>
      ) : null}
    </dl>
  );
}

export function statusLabel(
  state: ToolExecutionDto["execution_state"],
): string {
  return state.replaceAll("_", " ");
}

export function stringValue(value: JsonValue | undefined): string | null {
  return typeof value === "string" ? value : null;
}
