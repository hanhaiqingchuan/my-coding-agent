import { ApiError, ApiClient } from "./client";
import type { ClientCommand, ServerMessage } from "./types";

export const WS_AUTH_EXPIRED = 4401;
export const WS_SUBPROTOCOL = "coding-agent";
const RECONNECT_DELAY_MS = 250;
const MAX_RECONNECT_DELAY_MS = 2_000;

export type SocketConnectionState =
  "connecting" | "connected" | "reconnecting" | "offline";

type SocketLike = {
  onopen: WebSocket["onopen"];
  onmessage: WebSocket["onmessage"];
  onclose: WebSocket["onclose"];
  onerror: WebSocket["onerror"];
  send(data: string): void;
  close(): void;
};

type SocketFactory = (url: string, protocols: string[]) => SocketLike;

type SessionSocketOptions = {
  api: ApiClient;
  onMessage(message: ServerMessage): void;
  onConnection(connection: SocketConnectionState): void;
  onToken(token: string | null): void;
  createSocket?: SocketFactory;
};

export class SessionSocket {
  private socket: SocketLike | null = null;
  private sessionId: string | null = null;
  private generation = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly options: SessionSocketOptions) {}

  async connect(sessionId: string): Promise<void> {
    this.close();
    this.sessionId = sessionId;
    this.options.onConnection("connecting");
    await this.open(sessionId, 0, 0, this.generation);
  }

  close(): void {
    this.generation += 1;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.socket?.close();
    this.socket = null;
    this.sessionId = null;
    this.options.onConnection("offline");
  }

  send(command: ClientCommand): void {
    this.socket?.send(JSON.stringify(command));
  }

  private async open(
    sessionId: string,
    authRetries: number,
    reconnects: number,
    generation: number,
  ): Promise<void> {
    if (!this.isCurrent(generation, sessionId)) return;
    let bootstrap;
    try {
      bootstrap = await this.options.api.bootstrap();
    } catch (error) {
      if (!this.isCurrent(generation, sessionId)) return;
      if (
        error instanceof ApiError &&
        error.status === 403 &&
        authRetries < 1
      ) {
        this.options.api.clearToken();
        this.options.onToken(null);
        if (!this.isCurrent(generation, sessionId)) return;
        await this.open(sessionId, authRetries + 1, reconnects, generation);
        return;
      }
      this.scheduleReconnect(sessionId, authRetries, reconnects, generation);
      return;
    }
    if (!this.isCurrent(generation, sessionId)) {
      return;
    }
    this.options.onToken(bootstrap.csrf_token);
    const factory =
      this.options.createSocket ??
      ((url, protocols) => new WebSocket(url, protocols));
    const socket = factory(bootstrap.websocket_url, [
      WS_SUBPROTOCOL,
      bootstrap.csrf_token,
    ]);
    this.socket = socket;
    socket.onopen = () => {
      if (generation !== this.generation || this.sessionId !== sessionId)
        return;
      this.options.onConnection("connected");
      this.send({
        type: "session.subscribe",
        client_command_id: crypto.randomUUID(),
        session_id: sessionId,
        payload: {},
      });
    };
    socket.onmessage = (event: MessageEvent) => {
      if (generation !== this.generation) return;
      try {
        this.options.onMessage(JSON.parse(event.data) as ServerMessage);
      } catch {
        // Invalid wire data is ignored; a fresh snapshot remains authoritative.
      }
    };
    socket.onerror = () => {
      if (generation === this.generation)
        this.options.onConnection("reconnecting");
    };
    socket.onclose = (event) => {
      if (generation !== this.generation || this.sessionId !== sessionId)
        return;
      this.socket = null;
      if (event.code === WS_AUTH_EXPIRED) {
        if (authRetries < 1) {
          this.options.api.clearToken();
          this.options.onToken(null);
          this.options.onConnection("reconnecting");
          void this.open(sessionId, authRetries + 1, reconnects, generation);
          return;
        }
        this.options.onConnection("offline");
        return;
      }
      // Every ordinary reconnect goes through the bounded backoff: a server that
      // accepts a connection and closes it at once would otherwise spin this loop.
      this.scheduleReconnect(sessionId, authRetries, reconnects, generation);
    };
  }

  private scheduleReconnect(
    sessionId: string,
    authRetries: number,
    reconnects: number,
    generation: number,
  ): void {
    if (!this.isCurrent(generation, sessionId) || this.reconnectTimer !== null)
      return;
    this.options.onConnection("reconnecting");
    const delay = Math.min(
      RECONNECT_DELAY_MS * 2 ** Math.min(reconnects, 3),
      MAX_RECONNECT_DELAY_MS,
    );
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.open(sessionId, authRetries, reconnects + 1, generation);
    }, delay);
  }

  private isCurrent(generation: number, sessionId: string): boolean {
    return generation === this.generation && this.sessionId === sessionId;
  }
}
