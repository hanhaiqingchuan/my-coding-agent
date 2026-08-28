import { useCallback, useEffect, useReducer, useRef } from "react";

import { SessionSocket } from "../../api/socket";
import type { ApiClient } from "../../api/client";
import {
  createInitialSessionViewState,
  sessionViewReducer,
} from "./sessionReducer";

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

    async function refreshSnapshot() {
      refreshRequested = true;
      if (refreshing) return;
      refreshing = true;
      try {
        while (active && refreshRequested) {
          refreshRequested = false;
          const snapshot = await api.snapshot(activeSessionId);
          if (!active) return;
          dispatch({ type: "snapshot.refreshed", snapshot });
        }
      } catch {
        // A reconnect subscription supplies a fresh snapshot after transient failures.
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
      socket.close();
      if (socketRef.current === socket) socketRef.current = null;
    };
  }, [api, sessionId]);

  const send = useCallback((command: Parameters<SessionSocket["send"]>[0]) => {
    socketRef.current?.send(command);
  }, []);

  return { state, dispatch, send };
}
