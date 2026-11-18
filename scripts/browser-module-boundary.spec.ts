// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { checkBrowserModuleIds, checkPicomatchConsumers } from './browser-module-boundary'

describe('browser module boundary', () => {
  it('allows the reviewed build-tool edges and rejects a new consumer or alias', () => {
    const reviewed = { '': {}, 'node_modules/fdir': { peerDependencies: { picomatch: '^4' } } }
    expect(() => checkPicomatchConsumers(reviewed)).not.toThrow()
    expect(() => checkPicomatchConsumers({ ...reviewed, '': { dependencies: { picomatch: '^4' } } }))
      .toThrow('Unreviewed picomatch consumer: <root>')
    expect(() => checkPicomatchConsumers({ ...reviewed, 'node_modules/feature': {
      optionalDependencies: { matcher: 'npm:picomatch@4' },
    } })).toThrow('Unreviewed picomatch consumer: feature')
  })

  it('examines all chunks and rejects actual nested module IDs in lazy chunks', () => {
    const entry = { type: 'chunk' as const, fileName: 'entry.js', modules: { '/src/main.ts': {} } }
    expect(checkBrowserModuleIds([{ type: 'asset' }, entry])).toEqual({ chunks: 1, modules: 1 })
    expect(() => checkBrowserModuleIds([entry, { type: 'chunk', fileName: 'lazy.js', modules: {
      'C:\\app\\node_modules\\other\\node_modules\\picomatch\\index.js': {},
    } }])).toThrow('picomatch is reachable in browser chunk lazy.js')
    expect(() => checkBrowserModuleIds([{ type: 'asset' }])).toThrow('No browser module IDs')
  })
})
