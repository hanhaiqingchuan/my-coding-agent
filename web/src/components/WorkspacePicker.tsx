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
      setError("请输入绝对路径的工作区目录。");
      return;
    }
    try {
      await onCreate(workspace.trim(), title.trim() || null);
      setWorkspace("");
      setTitle("");
      setError(null);
    } catch {
      setError("无法打开该工作区。");
    }
  }

  return (
    <form className="workspace-picker" onSubmit={submit}>
      <h1>My Coding Agent</h1>
      <label>
        工作区
        <input
          value={workspace}
          onChange={(event) => setWorkspace(event.target.value)}
          placeholder="/绝对路径/工作区目录"
        />
      </label>
      <label>
        会话名称 <span aria-hidden="true">（可选）</span>
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      {error ? <p role="alert">{error}</p> : null}
      <button type="submit">打开工作区</button>
    </form>
  );
}
