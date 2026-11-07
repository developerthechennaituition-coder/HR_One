import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import fs from "node:fs";
import path from "node:path";

const certDirectory = path.resolve(import.meta.dirname, "../certs");
const keyPath = path.join(certDirectory, "localhost-key.pem");
const certPath = path.join(certDirectory, "localhost.pem");
const hasLocalCertificate = fs.existsSync(keyPath) && fs.existsSync(certPath);

// FastAPI backend (plain HTTP). The browser only talks to the Vite origin; the
// proxy forwards /api to the backend, so HTTPS pages never call the backend
// directly. Override with VITE_BACKEND_PROXY_TARGET if the backend runs elsewhere.
const backendTarget = process.env.VITE_BACKEND_PROXY_TARGET || "http://127.0.0.1:8000";
const apiProxy = {
  "/api": {
    target: backendTarget,
    changeOrigin: true,
  },
};

export default defineConfig({
  plugins: [react()],
  preview: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    proxy: apiProxy,
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    ...(hasLocalCertificate
      ? {
          https: {
            key: fs.readFileSync(keyPath),
            cert: fs.readFileSync(certPath),
          },
        }
      : {}),
    proxy: apiProxy,
  },
});
