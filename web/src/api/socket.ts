import { ApiError, ApiClient } from "./client";
import type { ClientCommand, ServerMessage } from "./types";

export const WS_AUTH_EXPIRED = 4401;
export const WS_SUBPROTOCOL = "coding-agent";
const MAX_AUTOMATIC_RECONNECTS = 1;

export type SocketConnectionState = "connecting" | "connected" | "reconnecting" | "offline";

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

  constructor(private readonly options: SessionSocketOptions) {}

  async connect(sessionId: string): Promise<void> {
    this.close();
    this.sessionId = sessionId;
    this.options.onConnection("connecting");
    await this.open(sessionId, 0, 0, this.generation);
  }

  close(): void {
    this.generation += 1;
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
    let bootstrap;
    try {
      bootstrap = await this.options.api.bootstrap();
    } catch (error) {
      if (error instanceof ApiError && error.status === 403 && authRetries < 1) {
        this.options.api.clearToken();
        this.options.onToken(null);
        await this.open(sessionId, authRetries + 1, reconnects, generation);
        return;
      }
      if (generation === this.generation) {
        this.options.onConnection("offline");
      }
      return;
    }
    if (generation !== this.generation || this.sessionId !== sessionId) {
      return;
    }
    this.options.onToken(bootstrap.csrf_token);
    const factory = this.options.createSocket ?? ((url, protocols) => new WebSocket(url, protocols));
    const socket = factory(bootstrap.websocket_url, [WS_SUBPROTOCOL, bootstrap.csrf_token]);
    this.socket = socket;
    socket.onopen = () => {
      if (generation !== this.generation || this.sessionId !== sessionId) return;
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
      if (generation === this.generation) this.options.onConnection("reconnecting");
    };
    socket.onclose = (event) => {
      if (generation !== this.generation || this.sessionId !== sessionId) return;
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
      if (reconnects < MAX_AUTOMATIC_RECONNECTS) {
        this.options.onConnection("reconnecting");
        void this.open(sessionId, authRetries, reconnects + 1, generation);
        return;
      }
      this.options.onConnection("offline");
    };
  }
}
