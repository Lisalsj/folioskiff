import { shellText as t } from '@/stores/locale'
import { apiFetch } from '@/lib/api'

export class ApiResponseError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(messageForDetail(detail, status))
    this.name = 'ApiResponseError'
  }
}

function messageForDetail(detail: unknown, status: number): string {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) => {
      if (typeof item !== 'object' || item === null) return []
      const message = Reflect.get(item, 'msg')
      if (typeof message !== 'string' || !message.trim()) return []
      const location = Reflect.get(item, 'loc')
      if (Array.isArray(location) && location.length) {
        const path = location.filter((part) => typeof part === 'string' || typeof part === 'number').join('.')
        return [path ? `${path}：${message}` : message]
      }
      return [message]
    })
    if (messages.length) return `${messages.join('；')}（${status}）`
  }
  if (typeof detail === 'object' && detail !== null) {
    const message = Reflect.get(detail, 'message')
    const code = Reflect.get(detail, 'code')
    if (typeof message === 'string' && message.trim()) {
      return typeof code === 'string' && code.trim()
        ? `${message}（${code}）`
        : message
    }
  }
  return t('请求失败（{0}）', status)
}

export async function apiJson<T>(input: RequestInfo | URL, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const isFormData = typeof FormData !== 'undefined' && init.body instanceof FormData
  if (init.body !== undefined && !isFormData && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await apiFetch(input, { ...init, headers })
  await requireSuccess(response)
  return (await response.json()) as T
}

export async function apiVoid(input: RequestInfo | URL, init: RequestInit = {}): Promise<void> {
  const response = await apiFetch(input, init)
  await requireSuccess(response)
}

async function requireSuccess(response: Response): Promise<void> {
  if (response.ok) return
  let detail: unknown = response.statusText
  try {
    const payload = (await response.json()) as { detail?: unknown }
    detail = payload.detail ?? detail
  } catch {
    // Keep a stable error for empty or non-JSON provider responses.
  }
  throw new ApiResponseError(response.status, detail)
}
