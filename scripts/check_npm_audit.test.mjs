import assert from 'node:assert/strict'
import test from 'node:test'
import { reviewAudit } from './check_npm_audit.mjs'

const lock = { packages: { 'node_modules/picomatch': { version: '4.0.7' } } }
const reviewDate = Date.parse('2026-11-18T00:00:00Z')
function report() {
  return {
    auditReportVersion: 2,
    metadata: { vulnerabilities: { total: 3 } },
    vulnerabilities: {
      picomatch: { nodes: ['node_modules/picomatch'], via: [{
        name: 'picomatch', severity: 'high', range: '<=4.0.7',
        url: 'https://github.com/advisories/GHSA-fc5q-39xc-vjpf',
      }] },
      tinyglobby: { via: ['picomatch'] },
      vite: { via: ['tinyglobby', 'picomatch'] },
    },
  }
}

test('accepts only the reviewed lock and advisory chain within the review period', () => {
  assert.equal(reviewAudit(report(), 1, lock, reviewDate), true)
  assert.throws(() => reviewAudit(report(), 1, lock, Date.parse('2026-12-18T00:00:00Z')))
  assert.throws(() => reviewAudit(report(), 1, { packages: {} }, reviewDate))
  assert.throws(() => reviewAudit(report(), 2, lock, reviewDate))
})

test('rejects new direct advisories, unaccounted findings and broken chains', () => {
  for (const via of [[{ name: 'other' }], [], ['missing'], ['vite']]) {
    const audit = report()
    audit.vulnerabilities.tinyglobby.via = via
    assert.throws(() => reviewAudit(audit, 1, lock, reviewDate))
  }
  const audit = report()
  audit.metadata.vulnerabilities.total = 4
  assert.throws(() => reviewAudit(audit, 1, lock, reviewDate))
})

test('accepts a clean audit after expiry and rejects registry errors', () => {
  const clean = { auditReportVersion: 2, metadata: { vulnerabilities: { total: 0 } }, vulnerabilities: {} }
  assert.equal(reviewAudit(clean, 0, lock, Date.parse('2027-01-01')), false)
  assert.throws(() => reviewAudit({ ...clean, error: 'registry unavailable' }, 1, lock))
})
