import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

// App build: standalone page for `ytrace ui`
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "dist/app",
    emptyOutDir: true,
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
});
