import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'
import { browserModuleBoundary } from './scripts/browser-module-boundary.ts'

function stableVendorChunk(moduleId: string): string | undefined {
  const id = moduleId.replaceAll('\\', '/')
  if (!id.includes('/node_modules/')) return undefined
  if (id.includes('/pdfjs-dist/')) return 'pdfjs'
  if (
    id.includes('/reka-ui/')
    || id.includes('/@floating-ui/')
    || id.includes('/reka-ui/node_modules/@vueuse/')
  ) return 'reka'
  if (
    id.includes('/node_modules/vue/')
    || id.includes('/node_modules/@vue/')
    || id.includes('/node_modules/vue-router/')
    || id.includes('/node_modules/pinia/')
  ) return 'vue-core'
  return undefined
}

export default defineConfig({
  plugins: [vue(), browserModuleBoundary()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./web/src', import.meta.url)),
    },
  },
  build: {
    outDir: 'src/paper_reader/static',
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      output: {
        codeSplitting: {
          groups: [{
            debugName: 'stable-vendors',
            name: (moduleId) => stableVendorChunk(moduleId) ?? null,
            includeDependenciesRecursively: false,
          }],
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['web/src/**/*.spec.ts', 'scripts/browser-module-boundary.spec.ts'],
  },
})
