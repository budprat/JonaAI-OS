import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/ws": {
        target: "ws://localhost:8080",
        ws: true,
      },
      "/api": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
      "/render-raster": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
      "/tile-proxy": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
      "/saved-jobs": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
      "/pixel-value": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
      "/raster-stats": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: process.env.NODE_ENV === "development",
    rollupOptions: {
      output: {
        manualChunks: {
          "react-vendor": ["react", "react-dom"],
          "map-vendor": ["leaflet", "react-leaflet"],
          "chart-vendor": ["recharts"],
          "markdown-vendor": [
            "react-markdown",
            "remark-gfm",
            "react-syntax-highlighter",
          ],
          "ui-vendor": [
            "@radix-ui/react-dialog",
            "@radix-ui/react-dropdown-menu",
            "@radix-ui/react-select",
            "@radix-ui/react-tabs",
            "@radix-ui/react-toast",
            "@radix-ui/react-tooltip",
            "@radix-ui/react-scroll-area",
          ],
        },
      },
    },
    chunkSizeWarningLimit: 600,
  },
});
