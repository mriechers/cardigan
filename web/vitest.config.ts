import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config'

// Inherits plugins + the `@` path alias from vite.config.ts.
// `globals: true` is required for @testing-library/react's auto-cleanup,
// which registers itself via a global afterEach — without it, rendered
// components leak between tests.
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      globals: true,
    },
  })
)
