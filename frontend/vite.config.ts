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
    // React 19.2's dev scheduler reads `window.event` unconditionally when
    // flushing work from a setImmediate task (react-dom-client
    // performWorkOnRootViaSchedulerTask). Work still queued when vitest
    // tears down the jsdom environment fires with no `window` and fails
    // the whole run despite every test passing — an upstream race the
    // setupTests afterAll drain mitigates but cannot fully close (see
    // jaegertracing/jaeger-ui#4339). Return false = ignore, and ONLY for
    // this exact signature: a ReferenceError with that message raised from
    // the react-dom scheduler itself. App-code ReferenceErrors carry a
    // different stack and still fail the run.
    onUnhandledError: (error) => {
      // Errors cross the fork boundary as plain serialized objects, so
      // match structurally instead of with instanceof.
      const message = (error as { message?: string } | undefined)?.message;
      if (message !== "window is not defined") {
        return true;
      }
      const stack = ((error as { stack?: string } | undefined)?.stack) ?? "";
      return !(stack.includes("performWorkOnRootViaSchedulerTask")
        && stack.includes("react-dom"));
    },
  },
})
