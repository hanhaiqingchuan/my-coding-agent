import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import type { ApiClient } from "../api/client";
import type { DirectoryEntryDto } from "../api/types";

type NewSessionDialogProps = {
  api: ApiClient;
  open: boolean;
  /** Where the visual browser starts: the open session's workspace, else /. */
  startPath?: string | null;
  onClose(): void;
  onCreate(workspace: string, title: string | null): Promise<void>;
};

/** Filesystem roots differ per platform; the home directory is always sane. */
const BROWSER_HOME = "/";

/**
 * The session-creation dialog: type the workspace path directly, or browse the
 * local filesystem visually (directories only, via the read-only listing
 * endpoint) and take the browsed directory. Everything resolves on submit
 * through the same create-session API the typed path always used.
 */
export function NewSessionDialog({
  api,
  open,
  startPath = null,
  onClose,
  onCreate,
}: NewSessionDialogProps) {
  const [workspace, setWorkspace] = useState("");
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [browsing, setBrowsing] = useState(false);
  const [browserPath, setBrowserPath] = useState<string | null>(null);
  const [entries, setEntries] = useState<DirectoryEntryDto[]>([]);
  const [browserError, setBrowserError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  const browse = useCallback(
    async (path: string) => {
      setBrowserError(null);
      try {
        const listing = await api.browseDirectories(path);
        setBrowserPath(listing.path);
        setEntries(
          listing.directories.filter((entry) => !entry.name.startsWith(".")),
        );
      } catch {
        setBrowserError("无法读取该目录。");
      }
    },
    [api],
  );

  // Opening the dialog resets the form and focuses the path field.
  useEffect(() => {
    if (!open) return;
    setWorkspace("");
    setTitle("");
    setError(null);
    setBrowsing(false);
    setBrowserPath(null);
    setEntries([]);
    dialogRef.current?.querySelector("input")?.focus();
  }, [open]);

  useEffect(() => {
    if (browsing && browserPath === null)
      void browse(startPath ?? BROWSER_HOME);
  }, [browsing, browserPath, browse, startPath]);

  if (!open) return null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!workspace.trim()) {
      setError("请填写或浏览选择工作区目录。");
      return;
    }
    try {
      await onCreate(workspace.trim(), title.trim() || null);
      onClose();
    } catch {
      setError("无法打开该工作区。");
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  }

  const parent =
    browserPath !== null && browserPath !== "/"
      ? browserPath.slice(0, browserPath.lastIndexOf("/")) || "/"
      : null;

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div
        ref={dialogRef}
        className="new-session-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-session-title"
        onKeyDown={onKeyDown}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="new-session-title">创建新会话</h2>
        <form onSubmit={submit}>
          <label>
            工作区
            <div className="dialog-workspace-row">
              <input
                value={workspace}
                onChange={(event) => setWorkspace(event.target.value)}
                placeholder="/绝对路径/工作区目录"
                aria-label="工作区"
              />
              <button
                type="button"
                className="dialog-browse-toggle"
                aria-pressed={browsing}
                onClick={() => setBrowsing((current) => !current)}
              >
                浏览…
              </button>
            </div>
          </label>
          {browsing ? (
            <div className="dir-browser" aria-label="目录浏览器">
              <p className="dir-browser-path">{browserPath ?? "…"}</p>
              <ul>
                {parent !== null ? (
                  <li>
                    <button type="button" onClick={() => void browse(parent)}>
                      ..（上一级）
                    </button>
                  </li>
                ) : null}
                {entries.map((entry) => (
                  <li key={entry.path}>
                    <button
                      type="button"
                      onClick={() => void browse(entry.path)}
                    >
                      {entry.name}
                    </button>
                  </li>
                ))}
                {entries.length === 0 && browserError === null ? (
                  <li className="dir-browser-empty">此目录没有子目录</li>
                ) : null}
              </ul>
              {browserError !== null ? (
                <p className="dir-browser-error" role="alert">
                  {browserError}
                </p>
              ) : null}
              <button
                type="button"
                className="dir-browser-pick"
                disabled={browserPath === null}
                onClick={() => {
                  if (browserPath !== null) {
                    setWorkspace(browserPath);
                    setBrowsing(false);
                  }
                }}
              >
                选择当前目录
              </button>
            </div>
          ) : null}
          <label>
            会话名称 <span aria-hidden="true">（可选）</span>
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              aria-label="会话名称（可选）"
            />
          </label>
          {error ? (
            <p role="alert" className="dialog-error">
              {error}
            </p>
          ) : null}
          <div className="dialog-actions">
            <button type="button" className="dialog-cancel" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="dialog-submit">
              打开工作区
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
