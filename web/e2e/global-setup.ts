import { request, type FullConfig } from '@playwright/test'
import { mkdir, readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

type InstanceState = {
  activation_secret: string
  created_at: string
  pid: number
  port: number
  schema_version: string
}

type ActivationResponse = {
  bootstrap_token: string
  expires_in_seconds: number
  pid: number
}

const INSTANCE_FIELDS = [
  'activation_secret',
  'created_at',
  'pid',
  'port',
  'schema_version',
] as const

const ONBOARDING_STEPS = [
  ['storage_privacy', 'completed'],
  ['agents', 'skipped'],
  ['sources', 'skipped'],
  ['reading_preferences', 'skipped'],
] as const

function baseUrl(config: FullConfig): string {
  const configured = config.projects[0]?.use.baseURL
  if (typeof configured !== 'string') {
    throw new Error('Playwright baseURL is required for local-session setup')
  }
  return configured
}

async function readInstanceState(stateRoot: string, expectedPort: number): Promise<InstanceState> {
  const raw: unknown = JSON.parse(
    await readFile(resolve(stateRoot, 'instance.json'), { encoding: 'utf8' }),
  )
  const keys = typeof raw === 'object' && raw !== null ? Object.keys(raw).sort() : []
  if (
    typeof raw !== 'object'
    || raw === null
    || keys.length !== INSTANCE_FIELDS.length
    || keys.some((key, index) => key !== INSTANCE_FIELDS[index])
    || !('schema_version' in raw)
    || raw.schema_version !== 'paper-reader-instance-v1'
    || !('pid' in raw)
    || typeof raw.pid !== 'number'
    || !Number.isSafeInteger(raw.pid)
    || raw.pid < 1
    || raw.pid > 2 ** 32 - 1
    || !('port' in raw)
    || raw.port !== expectedPort
    || !('created_at' in raw)
    || typeof raw.created_at !== 'string'
    || raw.created_at.length > 64
    || Number.isNaN(Date.parse(raw.created_at))
    || !('activation_secret' in raw)
    || typeof raw.activation_secret !== 'string'
    || !/^[A-Za-z0-9_-]{43,128}$/.test(raw.activation_secret)
  ) {
    throw new Error('Local E2E instance state does not match its closed contract')
  }
  return raw as InstanceState
}

async function expectOk(response: { ok(): boolean; status(): number }, operation: string) {
  if (!response.ok()) {
    throw new Error(`${operation} failed with status ${response.status()}`)
  }
}

export default async function globalSetup(config: FullConfig): Promise<void> {
  const url = new URL(baseUrl(config))
  const stateRoot = resolve('.tmp/playwright-e2e/state')
  const browserState = resolve('.tmp/playwright-e2e/browser-state.json')
  const instance = await readInstanceState(stateRoot, Number(url.port))
  const api = await request.newContext({ baseURL: url.origin })

  try {
    const activation = await api.post('/api/v1/session/activate', {
      data: { activation_secret: instance.activation_secret },
    })
    await expectOk(activation, 'Local instance activation')
    const grant: unknown = await activation.json()
    if (
      typeof grant !== 'object'
      || grant === null
      || Object.keys(grant).sort().join(',') !== 'bootstrap_token,expires_in_seconds,pid'
      || !('bootstrap_token' in grant)
      || typeof grant.bootstrap_token !== 'string'
      || !/^[A-Za-z0-9_-]{43,128}$/.test(grant.bootstrap_token)
      || !('expires_in_seconds' in grant)
      || typeof grant.expires_in_seconds !== 'number'
      || !Number.isSafeInteger(grant.expires_in_seconds)
      || grant.expires_in_seconds < 1
      || grant.expires_in_seconds > 300
      || !('pid' in grant)
      || grant.pid !== instance.pid
    ) {
      throw new Error('Local instance activation returned an invalid browser grant')
    }
    const browserGrant = grant as ActivationResponse

    const bootstrap = await api.post('/api/v1/session/bootstrap', {
      data: { token: browserGrant.bootstrap_token },
      headers: { Origin: url.origin },
    })
    await expectOk(bootstrap, 'Local session bootstrap')

    const csrf = (await api.storageState()).cookies.find(
      cookie => cookie.name === 'paper_reader_csrf',
    )?.value
    if (!csrf) {
      throw new Error('Local session bootstrap did not establish CSRF protection')
    }

    for (const [step, status] of ONBOARDING_STEPS) {
      const updated = await api.put(`/api/v1/onboarding/steps/${step}`, {
        data: { status },
        headers: {
          Origin: url.origin,
          'X-CSRF-Token': csrf,
        },
      })
      await expectOk(updated, `Onboarding fixture step ${step}`)
    }

    const overview = await api.get('/api/v1/onboarding')
    await expectOk(overview, 'Onboarding fixture verification')
    const snapshot = await overview.json() as { state?: { completed?: boolean } }
    if (snapshot.state?.completed !== true) {
      throw new Error('Onboarding fixture did not reach its completed state')
    }

    await mkdir(resolve(browserState, '..'), { recursive: true })
    await api.storageState({ path: browserState })
  } finally {
    await api.dispose()
  }
}
