/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Direct backend by default; API_PROXY_TARGET points dev/preview at
      // another front door (e.g. the compose nginx on :8080 that also mounts
      // the workspace volumes) without touching code.
      "/api": process.env.API_PROXY_TARGET ?? "http://localhost:8000",
    },
  },
  build: {
    rolldownOptions: {
      output: {
        // Rolldown manual chunking (`codeSplitting` supersedes the older
        // `advancedChunks` name in rolldown >= 1.2): keep antd and the React
        // runtime in a single long-cacheable vendor chunk.
        codeSplitting: {
          groups: [
            {
              name: "vendor",
              test: /node_modules[\\/](react|react-dom|scheduler|antd|rc-[a-z-]+|@ant-design)/,
            },
          ],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/setupTests.ts",
  },
})
