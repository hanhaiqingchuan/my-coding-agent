import { useCallback, useEffect, useReducer, useRef } from "react";

import { SessionSocket } from "../../api/socket";
import type { ApiClient } from "../../api/client";
import {
  createInitialSessionViewState,
  sessionViewReducer,
} from "./sessionReducer";

const SNAPSHOT_REFRESH_MAX_RETRIES = 3;
const SNAPSHOT_REFRESH_BASE_DELAY_MS = 200;

export function useSession(api: ApiClient, sessionId: string | null) {
  const [state, dispatch] = useReducer(
    sessionViewReducer,
    undefined,
    createInitialSessionViewState,
  );
  const socketRef = useRef<SessionSocket | null>(null);

  useEffect(() => {
    if (sessionId === null) {
      dispatch({ type: "session.selected" });
      return undefined;
    }
    const activeSessionId = sessionId;
    let active = true;
    let refreshRequested = false;
    let refreshing = false;
    let cancelBackoff: (() => void) | null = null;

    function backoff(attempt: number): Promise<void> {
      return new Promise((resolve) => {
        const timer = setTimeout(
          () => {
            cancelBackoff = null;
            resolve();
          },
          SNAPSHOT_REFRESH_BASE_DELAY_MS * 2 ** attempt,
        );
        cancelBackoff = () => {
          clearTimeout(timer);
          cancelBackoff = null;
          resolve();
        };
      });
    }

    async function refreshSnapshot() {
      refreshRequested = true;
      if (refreshing) return;
      refreshing = true;
      try {
        while (active && refreshRequested) {
          refreshRequested = false;
          for (
            let attempt = 0;
            active && attempt <= SNAPSHOT_REFRESH_MAX_RETRIES;
            attempt += 1
          ) {
            try {
              const snapshot = await api.snapshot(activeSessionId);
              if (!active) return;
              dispatch({ type: "snapshot.refreshed", snapshot });
              break;
            } catch {
              // Retry a transient failure so the durable event that asked for
              // this refresh is not lost; once the bounded attempts are spent a
              // reconnect subscription remains the authoritative fallback.
              if (attempt === SNAPSHOT_REFRESH_MAX_RETRIES) break;
              await backoff(attempt);
            }
          }
        }
      } finally {
        refreshing = false;
      }
    }

    const socket = new SessionSocket({
      api,
      onMessage: (message) => {
        dispatch({ type: "server.message", message });
        if (message.type === "durable") void refreshSnapshot();
      },
      onConnection: (connection) =>
        dispatch({ type: "connection.changed", connection }),
      onToken: (csrfToken) => dispatch({ type: "csrf.changed", csrfToken }),
    });
    socketRef.current = socket;
    dispatch({ type: "session.selected" });
    void socket.connect(activeSessionId);
    return () => {
      active = false;
      cancelBackoff?.();
      socket.close();
      if (socketRef.current === socket) socketRef.current = null;
    };
  }, [api, sessionId]);

  const send = useCallback((command: Parameters<SessionSocket["send"]>[0]) => {
    socketRef.current?.send(command);
  }, []);

  return { state, dispatch, send };
}
