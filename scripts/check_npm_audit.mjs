import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const advisory = 'https://github.com/advisories/GHSA-fc5q-39xc-vjpf'
const reviewedVersion = '4.0.7'
const expires = '2026-12-18T00:00:00Z'

// npm reports both the advisory and each package affected through that dependency.
export function reviewAudit(report, exitCode, lock, now = Date.now()) {
  const findings = report.vulnerabilities
  const total = report.metadata?.vulnerabilities?.total
  if (report.error || report.auditReportVersion !== 2 || !findings ||
      !Number.isInteger(total) || Object.keys(findings).length !== total) {
    throw new Error('Incomplete npm audit result')
  }
  if (total === 0 && exitCode === 0) return false
  if (exitCode !== 1 || total === 0 || now >= Date.parse(expires)) {
    throw new Error('Failing npm audit or expired security exception')
  }
  const direct = findings.picomatch
  if (direct?.nodes?.length !== 1 || direct.nodes[0] !== 'node_modules/picomatch' ||
      lock.packages?.[direct.nodes[0]]?.version !== reviewedVersion ||
      direct.via?.length !== 1 || direct.via[0]?.name !== 'picomatch' ||
      direct.via[0].url !== advisory || direct.via[0].severity !== 'high' ||
      direct.via[0].range !== '<=4.0.7') {
    throw new Error('npm findings differ from the reviewed picomatch advisory')
  }
  function leadsOnlyToPicomatch(name, path = new Set()) {
    if (name === 'picomatch') return true
    if (typeof name !== 'string' || path.has(name)) return false
    const via = findings[name]?.via
    return Array.isArray(via) && via.length > 0 &&
      via.every(dependency => leadsOnlyToPicomatch(dependency, new Set(path).add(name)))
  }
  for (const name of Object.keys(findings)) {
    if (!leadsOnlyToPicomatch(name)) throw new Error('Additional npm finding in ' + name)
  }
  return true
}

function runNpm(args) {
  if (!process.env.npm_execpath) throw new Error('Run with npm run audit')
  const result = spawnSync(process.execPath, [process.env.npm_execpath, ...args], {
    encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'], maxBuffer: 16 * 1024 * 1024,
  })
  if (result.error || result.status === null) throw result.error || new Error('npm interrupted')
  return { data: JSON.parse(result.stdout), status: result.status }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const audit = runNpm(['audit', '--audit-level=high', '--json'])
  const lock = JSON.parse(readFileSync('package-lock.json', 'utf8'))
  if (reviewAudit(audit.data, audit.status, lock)) {
    const latest = runNpm(['view', 'picomatch', 'dist-tags.latest', '--json', '--prefer-online'])
    if (latest.status !== 0 || latest.data !== reviewedVersion) {
      throw new Error('Reassess picomatch against the current registry release')
    }
    console.log('Build-tool exception until ' + expires + ': picomatch ' + reviewedVersion + ' ' + advisory)
  } else {
    console.log('npm audit: 0 known vulnerabilities.')
  }
}
