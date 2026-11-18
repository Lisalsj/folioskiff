import type { components } from '@/api/schema'

type OnboardingOverview = components['schemas']['OnboardingOverview']

export type HealthSnapshot = Readonly<Record<string, unknown>>

interface DiagnosticContext {
  generatedAt?: string
  platform?: string
}

function stringField(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

export function buildRedactedDiagnostic(
  health: HealthSnapshot,
  overview: OnboardingOverview,
  context: DiagnosticContext = {},
) {
  return {
    schema_version: 'paper-reader-redacted-diagnostic-v1',
    generated_at: context.generatedAt ?? new Date().toISOString(),
    platform: context.platform ?? 'unknown',
    application: {
      name: stringField(health.name),
      version: stringField(health.version),
      status: stringField(health.status),
      api_version: stringField(health.api_version),
      openapi_sha256: stringField(health.openapi_sha256),
      database_schema_revision: stringField(health.database_schema_revision),
      plugin_protocol_version: stringField(health.plugin_protocol_version),
    },
    storage: {
      writable: overview.storage.writable,
      state_writable: overview.storage.state_writable,
      cache_writable: overview.storage.cache_writable,
      library_writable: overview.storage.library_writable,
    },
    agents: overview.agents.map((agent) => ({
      backend_id: agent.backend_id,
      display_name: agent.display_name,
      location: agent.location,
      version: agent.version ?? null,
      probe_code: agent.probe_code,
      installed: agent.installed,
      authenticated: agent.authenticated,
      available: agent.available,
    })),
    sources: overview.sources.map((source) => ({
      connector_id: source.connector_id,
      display_name: source.display_name,
      enabled: source.enabled,
      credential_requirement: source.credential_requirement,
    })),
    retrieval: {
      lexical_enabled: overview.retrieval.lexical_enabled,
      semantic_enabled: overview.retrieval.semantic_enabled,
      model_state: overview.retrieval.model_state,
    },
  }
}
