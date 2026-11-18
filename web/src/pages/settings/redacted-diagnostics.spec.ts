import { describe, expect, it } from 'vitest'

import type { components } from '@/api/schema'

import { buildRedactedDiagnostic } from './redacted-diagnostics'

type OnboardingOverview = components['schemas']['OnboardingOverview']

describe('buildRedactedDiagnostic', () => {
  it('exports capability facts without paths, credentials, or unexpected fields', () => {
    const overview: OnboardingOverview = {
      state: {
        completed: true,
        current_step: 'complete',
        steps: {
          storage_privacy: 'completed',
          agents: 'completed',
          sources: 'completed',
          reading_preferences: 'completed',
        },
        privacy_acknowledged_at: '2026-08-01T00:00:00Z',
        updated_at: '2026-08-01T00:00:00Z',
      },
      storage: {
        writable: true,
        state_writable: true,
        cache_writable: true,
        library_writable: true,
        state_root: 'C:\\Users\\Private\\state',
        cache_root: 'C:\\Users\\Private\\cache',
        library_root: 'D:\\Private papers',
        backups_root: 'D:\\Private papers\\backups',
      },
      agents: [{
        backend_id: 'codex_cli',
        display_name: 'Codex CLI',
        location: 'cloud',
        version: '0.101.0',
        probe_code: 'ready',
        recommended_action: null,
        installed: true,
        authenticated: true,
        available: true,
        supports_manual_model_id: true,
        supports_default_model: true,
        models: [],
        degradation_reasons: ['agent-reason-secret'],
      }],
      sources: [{
        connector_id: 'ieee',
        display_name: 'IEEE Xplore',
        enabled: true,
        credential_requirement: 'api_key',
        credential_configured: true,
        disabled_reason: 'source-reason-secret',
        setup_url: 'https://developer.ieee.org/',
      }],
      retrieval: {
        lexical_enabled: true,
        semantic_enabled: false,
        model_state: 'not_installed',
        degradation_reasons: ['retrieval-reason-secret'],
      },
      blocking_issues: ['secret-blocking-issue'],
    }

    const diagnostic = buildRedactedDiagnostic(
      {
        name: 'paper-reader',
        version: '1.0.0',
        status: 'ok',
        api_version: 'v1',
        openapi_sha256: 'contract-hash',
        database_schema_revision: '0033',
        plugin_protocol_version: '1.0',
        unexpected_secret: 'do-not-export',
      },
      overview,
      { generatedAt: '2026-08-01T00:00:00.000Z', platform: 'Win32' },
    )
    const serialized = JSON.stringify(diagnostic)

    expect(diagnostic.application.version).toBe('1.0.0')
    expect(diagnostic.agents[0]?.probe_code).toBe('ready')
    expect(diagnostic.sources[0]).toEqual({
      connector_id: 'ieee',
      display_name: 'IEEE Xplore',
      enabled: true,
      credential_requirement: 'api_key',
    })
    expect(serialized).not.toContain('Private')
    expect(serialized).not.toContain('credential_configured')
    expect(serialized).not.toContain('do-not-export')
    expect(serialized).not.toContain('agent-reason-secret')
    expect(serialized).not.toContain('source-reason-secret')
    expect(serialized).not.toContain('retrieval-reason-secret')
    expect(serialized).not.toContain('secret-blocking-issue')
  })
})
