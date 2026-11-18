interface SessionBootstrapDependencies {
  fetcher?: typeof fetch
}

export class SessionBootstrapError extends Error {
  constructor(readonly status: number) {
    super(`Local session bootstrap failed with status ${status}`)
    this.name = 'SessionBootstrapError'
  }
}

export async function bootstrapLocalSession(
  dependencies: SessionBootstrapDependencies = {},
): Promise<boolean> {
  const parameters = new URLSearchParams(window.location.hash.slice(1))
  const token = parameters.get('bootstrap')
  if (!token) {
    return false
  }

  window.history.replaceState(
    window.history.state,
    '',
    `${window.location.pathname}${window.location.search}`,
  )

  const fetcher = dependencies.fetcher ?? window.fetch.bind(window)
  const response = await fetcher('/api/v1/session/bootstrap', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  })

  if (!response.ok) {
    throw new SessionBootstrapError(response.status)
  }
  return true
}
