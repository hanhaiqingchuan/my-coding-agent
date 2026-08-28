import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    passWithNoTests: true,
    // Vitest empties every CSS import while CSS processing is off, `?raw` included.
    // Only that raw form is let through, so a test can assert the stylesheet's own
    // state treatments; plain CSS imports stay stubbed and inject nothing.
    css: { include: [/\.css\?raw$/] },
  },
});
