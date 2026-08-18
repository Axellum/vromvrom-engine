import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Cible du moteur en développement (gui_server.py). Surchargeable via MOTEUR_API_URL.
const ENGINE_TARGET = process.env.MOTEUR_API_URL || "http://localhost:8000";

// Proxy des routes protégées du moteur vers gui_server pendant le dev (HMR Vite sur 5173).
// En prod, l'IHM est buildée puis servie par gui_server lui-même → même origine, pas de proxy.
const proxy = Object.fromEntries(
  ["/api", "/v1", "/ws", "/version"].map((p) => [
    p,
    { target: ENGINE_TARGET, changeOrigin: true, ws: p === "/ws" },
  ]),
);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy,
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
