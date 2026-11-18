type Fetcher = typeof fetch

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

function readCookie(name: string): string | undefined {
  const prefix = `${encodeURIComponent(name)}=`
  const cookie = document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : undefined
}

export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
  fetcher: Fetcher = window.fetch.bind(window),
): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)

  if (!SAFE_METHODS.has(method)) {
    const csrfToken = readCookie('paper_reader_csrf')
    if (csrfToken) {
      headers.set('X-CSRF-Token', csrfToken)
    }
  }

  return fetcher(input, {
    ...init,
    credentials: 'same-origin',
    headers,
  })
}
