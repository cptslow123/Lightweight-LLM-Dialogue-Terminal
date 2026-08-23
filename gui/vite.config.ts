import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: { ignored: ["**/src-tauri/target/**", "**/src-tauri/gen/**", "**/node_modules/**"] },
    proxy: { "/api": { target: "http://127.0.0.1:18765", changeOrigin: true } },
  },
});
