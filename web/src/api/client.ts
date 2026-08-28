import type { BootstrapDto, SessionDto, SessionSnapshotDto } from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class ApiClient {
  private csrfToken: string | null = null;

  async bootstrap(force = false): Promise<BootstrapDto> {
    if (!force && this.csrfToken !== null) {
      return this.request<BootstrapDto>("/api/bootstrap", { method: "GET" }, false);
    }
    const bootstrap = await this.request<BootstrapDto>("/api/bootstrap", { method: "GET" }, false);
    this.csrfToken = bootstrap.csrf_token;
    return bootstrap;
  }

  clearToken(): void {
    this.csrfToken = null;
  }

  getToken(): string | null {
    return this.csrfToken;
  }

  async listSessions(): Promise<SessionDto[]> {
    return this.request<SessionDto[]>("/api/sessions", { method: "GET" }, false);
  }

  async snapshot(sessionId: string): Promise<SessionSnapshotDto> {
    return this.request<SessionSnapshotDto>(`/api/sessions/${encodeURIComponent(sessionId)}/snapshot`, { method: "GET" }, false);
  }

  async createSession(workspace: string, title: string | null): Promise<SessionDto> {
    await this.bootstrap();
    return this.request<SessionDto>(
      "/api/sessions",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace, title }),
      },
      true,
    );
  }

  private async request<T>(path: string, init: RequestInit, stateChanging: boolean): Promise<T> {
    const headers = new Headers(init.headers);
    if (stateChanging && this.csrfToken !== null) {
      headers.set("X-CSRF-Token", this.csrfToken);
    }
    const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
    if (!response.ok) {
      if (response.status === 403) {
        this.clearToken();
      }
      throw new ApiError(response.status, `API request failed with status ${response.status}`);
    }
    return (await response.json()) as T;
  }
}
