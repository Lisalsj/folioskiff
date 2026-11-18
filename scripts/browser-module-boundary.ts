import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { Plugin } from 'vite'

type DependencyKind = 'dependencies' | 'devDependencies' | 'optionalDependencies' | 'peerDependencies'
type LockEntry = Partial<Record<DependencyKind, Record<string, string>>> & { name?: string }
const dependencyKinds: DependencyKind[] = ['dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies']

// These are the reviewed build-tool edges for the temporary picomatch advisory exception.
const reviewedConsumers = new Set([
  '@vue/language-core:dependencies', 'fdir:peerDependencies', 'tinyglobby:dependencies',
  'unplugin:dependencies', 'unplugin-utils:dependencies', 'vite:dependencies',
  'vitest:dependencies', 'vue-router:dependencies',
])

export function checkPicomatchConsumers(packages: Record<string, LockEntry>) {
  if (!packages || !Object.hasOwn(packages, '')) throw new Error('Missing root package in npm lock')
  for (const [location, entry] of Object.entries(packages)) {
    const owner = location ? (entry.name ?? location.split('node_modules/').at(-1)) : '<root>'
    for (const kind of dependencyKinds) {
      for (const [dependency, version] of Object.entries(entry[kind] ?? {})) {
        if (dependency !== 'picomatch' && !/^npm:picomatch(?:@|$)/.test(version)) continue
        if (dependency !== 'picomatch' || !reviewedConsumers.has(`${owner}:${kind}`)) {
          throw new Error(`Unreviewed picomatch consumer: ${owner} (${kind}.${dependency})`)
        }
      }
    }
  }
}

type OutputFile = { type: 'asset' } | { type: 'chunk'; fileName: string; modules: object }

export function checkBrowserModuleIds(files: OutputFile[]) {
  const modules = new Set<string>()
  let chunks = 0
  for (const file of files) {
    if (file.type !== 'chunk') continue
    chunks++
    for (const id of Object.keys(file.modules)) {
      const normalized = id.replaceAll('\\', '/')
      if (/(?:^|[/\0])node_modules\/picomatch(?:[/#?]|$)/.test(normalized)) {
        throw new Error(`picomatch is reachable in browser chunk ${file.fileName}: ${normalized}`)
      }
      modules.add(normalized)
    }
  }
  if (!modules.size) throw new Error('No browser module IDs available to audit')
  return { chunks, modules: modules.size }
}

export function browserModuleBoundary(): Plugin {
  return {
    name: 'check-browser-module-boundary',
    apply: 'build',
    enforce: 'post',
    configResolved(config) {
      const lock = JSON.parse(readFileSync(resolve(config.root, 'package-lock.json'), 'utf8'))
      checkPicomatchConsumers(lock.packages)
    },
    generateBundle(_options, bundle) {
      const result = checkBrowserModuleIds(Object.values(bundle))
      this.info(`Scanned ${result.modules} module IDs in ${result.chunks} browser chunks: 0 picomatch modules`)
    },
  }
}
