import { readFileSync, statSync } from 'node:fs'
import path from 'node:path'


const ENTRY_LIMIT_BYTES = 350_000
const FORBIDDEN_INITIAL_CHUNKS = [
  ['pdfjs', /(?:^|[/_.-])pdfjs(?:-dist)?(?:[/_.-]|$)/i],
  ['ocr', /(?:^|[/_.-])ocr(?:[/_.-]|$)/i],
  ['reader', /(?:readerworkspace|bilingualreader|[/_.-]reader[/_.-])/i],
  ['comparison', /(?:^|[/_.-])comparisons?(?:page)?(?:[/_.-]|$)/i],
  ['publishing', /(?:^|[/_.-])publishing(?:page)?(?:[/_.-]|$)/i],
  ['settings', /(?:^|[/_.-])settings(?:[/_.-]|$)/i],
]


function parseDist(argv) {
  if (argv.length === 0) return path.resolve('src/paper_reader/static')
  const distIndex = argv.indexOf('--dist')
  if (distIndex < 0 || !argv[distIndex + 1] || argv.length !== 2) {
    throw new Error('usage: node scripts/check_vite_budget.mjs --dist <directory>')
  }
  return path.resolve(argv[distIndex + 1])
}


function loadManifest(dist) {
  const manifestPath = path.join(dist, '.vite', 'manifest.json')
  return JSON.parse(readFileSync(manifestPath, 'utf8'))
}


function findHtmlEntry(manifest) {
  const entries = Object.entries(manifest).filter(([key, item]) => (
    item?.isEntry === true && String(item.src ?? key).endsWith('.html')
  ))
  if (entries.length !== 1) {
    throw new Error(`expected exactly one HTML entry, found ${entries.length}`)
  }
  return entries[0][0]
}


function staticClosure(manifest, entryKey) {
  const visited = new Set()

  function visit(key) {
    if (visited.has(key)) return
    const item = manifest[key]
    if (!item) throw new Error(`manifest import is missing: ${key}`)
    visited.add(key)
    for (const importedKey of item.imports ?? []) visit(importedKey)
  }

  visit(entryKey)
  return [...visited]
}


function assetPath(dist, file) {
  const resolved = path.resolve(dist, file)
  const relative = path.relative(dist, resolved)
  if (path.isAbsolute(relative) || relative === '..' || relative.startsWith(`..${path.sep}`)) {
    throw new Error(`asset path escapes distribution: ${file}`)
  }
  return resolved
}


function javascriptRows(dist, manifest, keys) {
  const files = new Set(keys
    .map((key) => manifest[key]?.file)
    .filter((file) => typeof file === 'string' && /\.m?js$/i.test(file)))
  return [...files]
    .map((file) => ({ file, bytes: statSync(assetPath(dist, file)).size }))
    .sort((left, right) => (
      left.file < right.file ? -1 : left.file > right.file ? 1 : 0
    ))
}


function findForbiddenChunk(manifest, keys) {
  const matches = []
  for (const key of keys) {
    const item = manifest[key] ?? {}
    const descriptor = [key, item.name, item.src, item.file]
      .filter((value) => typeof value === 'string')
      .join('/')
      .replaceAll('\\', '/')
    for (const [label, pattern] of FORBIDDEN_INITIAL_CHUNKS) {
      if (pattern.test(descriptor)) matches.push({ label, key })
    }
  }
  matches.sort((left, right) => {
    const leftKey = `${left.label}\0${left.key}`
    const rightKey = `${right.label}\0${right.key}`
    return leftKey < rightKey ? -1 : leftKey > rightKey ? 1 : 0
  })
  return matches[0]
}


function printRows(rows) {
  const total = rows.reduce((sum, row) => sum + row.bytes, 0)
  const lines = [
    'Initial JavaScript closure (raw bytes)',
    ...rows.map((row) => `${String(row.bytes).padStart(8)}  ${row.file}`),
    `${String(total).padStart(8)}  TOTAL (limit ${ENTRY_LIMIT_BYTES})`,
  ]
  process.stdout.write(`${lines.join('\n')}\n`)
  return total
}


const dist = parseDist(process.argv.slice(2))
const manifest = loadManifest(dist)
const entryKey = findHtmlEntry(manifest)
const closureKeys = staticClosure(manifest, entryKey)
const total = printRows(javascriptRows(dist, manifest, closureKeys))
const forbidden = findForbiddenChunk(manifest, closureKeys)
if (forbidden) {
  throw new Error(`forbidden initial chunk: ${forbidden.label} (${forbidden.key})`)
}
if (total > ENTRY_LIMIT_BYTES) {
  throw new Error(
    `initial JavaScript closure is ${total} bytes; limit is ${ENTRY_LIMIT_BYTES}`,
  )
}
