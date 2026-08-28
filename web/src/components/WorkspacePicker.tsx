import { useState, type FormEvent } from "react";

type WorkspacePickerProps = {
  onCreate(workspace: string, title: string | null): Promise<void>;
};

export function WorkspacePicker({ onCreate }: WorkspacePickerProps) {
  const [workspace, setWorkspace] = useState("");
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspace.trim()) {
      setError("Choose an absolute workspace directory.");
      return;
    }
    try {
      await onCreate(workspace.trim(), title.trim() || null);
      setWorkspace("");
      setTitle("");
      setError(null);
    } catch {
      setError("The workspace could not be opened.");
    }
  }

  return (
    <form className="workspace-picker" onSubmit={submit}>
      <h1>My Coding Agent</h1>
      <label>
        Workspace
        <input value={workspace} onChange={(event) => setWorkspace(event.target.value)} placeholder="/absolute/path" />
      </label>
      <label>
        Session title <span aria-hidden="true">(optional)</span>
        <input value={title} onChange={(event) => setTitle(event.target.value)} />
      </label>
      {error ? <p role="alert">{error}</p> : null}
      <button type="submit">Open workspace</button>
    </form>
  );
}
