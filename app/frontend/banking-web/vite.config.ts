import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  server: {
    port: 5170,
    host: mode === "oauth-local" ? "localhost" : "0.0.0.0",
    strictPort: mode === "oauth-local",
    https: mode === "oauth-local" ? {
      cert: readFileSync(path.join(homedir(), ".banking-oauth-local", "localhost-cert.pem")),
      key: readFileSync(path.join(homedir(), ".banking-oauth-local", "localhost-key.pem")),
    } : undefined,
  },
  plugins: [
    react()
  ].filter(Boolean),
  resolve: {
    alias: {
      "@": path.resolve(projectRoot, "./src"),
    },
  },
}));
