import type { JsonValue, ToolExecutionDto } from "../api/types";

type ToolCardProps = {
  tool: ToolExecutionDto;
  outputDraft?: string;
};

/** Longest text argument rendered in full; longer values are cut with a visible note. */
const MAX_TEXT_CHARACTERS = 2000;

/**
 * The write_file arguments the model can freeze, in the order they are shown. The schema
 * is operation/path/content/old_text/new_text/replace_all with additionalProperties
 * false, so no `diff` or `patch` key can ever arrive on a write card.
 */
const WRITE_ARGUMENTS: ReadonlyArray<{
  key: string;
  label: string;
  multiline: boolean;
}> = [
  { key: "operation", label: "Operation", multiline: false },
  { key: "path", label: "Path", multiline: false },
  { key: "replace_all", label: "Replace all", multiline: false },
  { key: "content", label: "Content", multiline: true },
  { key: "old_text", label: "Replaced text", multiline: true },
  { key: "new_text", label: "Replacement text", multiline: true },
];

type WriteRow = {
  label: string;
  value: string;
  multiline: boolean;
  truncationNote: string | null;
};

export function ToolCard({ tool, outputDraft }: ToolCardProps) {
  const isCommand =
    tool.name.includes("command") || stringValue(tool.input.command) !== null;
  const writeRows = tool.name.includes("write")
    ? writeArgumentRows(tool.input)
    : [];

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
        {isCommand ? <CommandDetails input={tool.input} /> : null}
        {writeRows.length > 0 ? <WriteDetails rows={writeRows} /> : null}
        {!isCommand && writeRows.length === 0 ? (
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
  input,
  metadata,
}: {
  input: Record<string, JsonValue>;
  metadata?: Record<string, JsonValue>;
}) {
  const command = stringValue(effectiveValue(input, metadata, "command"));
  const cwd = stringValue(effectiveValue(input, metadata, "cwd"));
  const reason = stringValue(effectiveValue(input, metadata, "reason"));
  const timeoutSeconds = effectiveValue(input, metadata, "timeout_seconds");
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

function WriteDetails({ rows }: { rows: WriteRow[] }) {
  return (
    <dl className="tool-write-details">
      {rows.map((row) => (
        <div
          key={row.label}
          className={row.multiline ? "tool-text-row" : undefined}
        >
          <dt>{row.label}</dt>
          <dd>
            {row.multiline ? <pre>{row.value}</pre> : row.value}
            {row.truncationNote !== null ? (
              <p className="tool-truncation">{row.truncationNote}</p>
            ) : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * The frozen write_file arguments that are actually present. A settled ToolExecutionDto
 * carries no `preview`, so the unified diff the approval card showed cannot be rebuilt
 * here; the card shows the arguments the backend froze instead of pretending they are a
 * diff. An input the tool never accepted yields no rows, and the caller then falls back
 * to the raw JSON so a card is never argument-less.
 */
function writeArgumentRows(input: Record<string, JsonValue>): WriteRow[] {
  const rows: WriteRow[] = [];
  for (const { key, label, multiline } of WRITE_ARGUMENTS) {
    const value = input[key];
    const text =
      typeof value === "string"
        ? value
        : typeof value === "boolean"
          ? String(value)
          : null;
    if (text === null) continue;
    const truncate = multiline && text.length > MAX_TEXT_CHARACTERS;
    rows.push({
      label,
      value: truncate ? text.slice(0, MAX_TEXT_CHARACTERS) : text,
      multiline,
      truncationNote: truncate
        ? `Showing the first ${MAX_TEXT_CHARACTERS} of ${text.length} characters.`
        : null,
    });
  }
  return rows;
}

/**
 * The effective value of one command argument. `metadata` holds what run_command actually
 * resolved and froze (the workspace-absolute cwd and the timeout in force), while `input`
 * holds only what the model sent, so metadata wins whenever it carries the key. A settled
 * ToolExecutionDto ships no metadata, so its card falls back to the frozen input and no
 * default is ever invented here.
 */
function effectiveValue(
  input: Record<string, JsonValue>,
  metadata: Record<string, JsonValue> | undefined,
  key: string,
): JsonValue | undefined {
  if (metadata !== undefined && key in metadata) return metadata[key];
  return input[key];
}

function timeoutLabel(timeoutSeconds: JsonValue | undefined): string {
  // Only a timeout the backend actually reported is shown; when neither the frozen
  // metadata nor the model input carries one, the schema default stays invisible
  // rather than being guessed here.
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
