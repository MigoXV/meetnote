import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { bridge } from "./proxy.mjs";

export default defineConfig({
  plugins: [
    react(),
    {
      name: "meetnote-demo-bridge",
      configureServer(server) {
        server.middlewares.use(bridge);
      },
      configurePreviewServer(server) {
        server.middlewares.use(bridge);
      },
    },
  ],
  server: { port: 5176, strictPort: true },
  preview: { port: 4176, strictPort: true },
});
