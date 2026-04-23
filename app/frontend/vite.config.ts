import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      manifest: {
        name: "HypertensionKG 智能工作台",
        short_name: "HypertensionKG",
        description: "高血压知识图谱与临床问答工作台",
        display: "standalone",
        start_url: "/",
        scope: "/",
        background_color: "#f4fbff",
        theme_color: "#0f766e",
        icons: [
          {
            src: "/pwa-icon.svg",
            sizes: "any",
            type: "image/svg+xml",
            purpose: "any maskable",
          },
        ],
      },
      workbox: {
        navigateFallback: "/index.html",
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff2}"],
        runtimeCaching: [
          {
            urlPattern: ({ url }) =>
              /^\/(api|sessions|admin|graph|qa|upload_file)(\/|$)/.test(url.pathname),
            handler: "NetworkOnly",
            options: {
              cacheName: "network-only-backend",
            },
          },
          {
            urlPattern: ({ request }) => ["script", "style", "font", "image"].includes(request.destination),
            handler: "CacheFirst",
            options: {
              cacheName: "static-assets",
              expiration: {
                maxEntries: 120,
                maxAgeSeconds: 60 * 60 * 24 * 30,
              },
            },
          },
        ],
      },
    }),
  ],
  server: {
    host: "0.0.0.0",
    port: 5173,
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/react") || id.includes("node_modules/react-dom") || id.includes("node_modules/react-router")) {
            return "vendor-react";
          }
          if (id.includes("node_modules/@tanstack/react-query") || id.includes("node_modules/@tanstack/query-core")) {
            return "vendor-query";
          }
          if (
            id.includes("node_modules/react-force-graph-2d") ||
            id.includes("node_modules/d3-") ||
            id.includes("node_modules/kapsule") ||
            id.includes("node_modules/three")
          ) {
            return "vendor-graph";
          }
          return undefined;
        },
      },
    },
  },
  test: {
    environment: "jsdom",
  },
});
