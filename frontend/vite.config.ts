/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
// Explicit .ts extension: tsconfig.node.json is moduleResolution nodenext
// (with allowImportingTsExtensions), unlike the app project.
import { isReactSchedulerTeardownArtifact } from './src/testing/reactTeardownArtifact.ts'

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
    // Ignore exactly one unhandled error: React's jsdom-teardown race.
    // The predicate lives in src/testing so it is unit-tested against the
    // real CI stacks — the previous inline version keyed on a single frame
    // name and failed a run in which every test passed. Return false =
    // ignore; anything else still fails the run.
    onUnhandledError: (error: unknown) => !isReactSchedulerTeardownArtifact(error),
  },
})
