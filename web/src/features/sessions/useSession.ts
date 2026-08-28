import { useCallback, useEffect, useReducer, useRef } from "react";

import { SessionSocket } from "../../api/socket";
import type { ApiClient } from "../../api/client";
import { createInitialSessionViewState, sessionViewReducer } from "./sessionReducer";

export function useSession(api: ApiClient, sessionId: string | null) {
  const [state, dispatch] = useReducer(sessionViewReducer, undefined, createInitialSessionViewState);
  const socketRef = useRef<SessionSocket | null>(null);

  useEffect(() => {
    if (sessionId === null) {
      dispatch({ type: "session.selected" });
      return undefined;
    }
    const socket = new SessionSocket({
      api,
      onMessage: (message) => dispatch({ type: "server.message", message }),
      onConnection: (connection) => dispatch({ type: "connection.changed", connection }),
      onToken: (csrfToken) => dispatch({ type: "csrf.changed", csrfToken }),
    });
    socketRef.current = socket;
    dispatch({ type: "session.selected" });
    void socket.connect(sessionId);
    return () => {
      socket.close();
      if (socketRef.current === socket) socketRef.current = null;
    };
  }, [api, sessionId]);

  const send = useCallback((command: Parameters<SessionSocket["send"]>[0]) => {
    socketRef.current?.send(command);
  }, []);

  return { state, dispatch, send };
}
