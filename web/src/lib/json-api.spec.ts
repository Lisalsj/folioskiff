import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiResponseError, apiJson, apiVoid } from './json-api'

describe('ApiResponseError', () => {
  afterEach(() => vi.unstubAllGlobals())

  it.each([apiJson, apiVoid])('preserves structured model failures for JSON and empty-response operations', async (request) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      detail: { code: 'output_limit', message: '请降低推理强度后重试。' },
    }), { status: 502 })))

    await expect(request('/api/v1/keywords/suggestions', { method: 'POST' })).rejects.toMatchObject({
      status: 502,
      message: '请降低推理强度后重试。（output_limit）',
      detail: { code: 'output_limit', message: '请降低推理强度后重试。' },
    })
  })
  it('explains FastAPI validation details instead of hiding them behind only 422', () => {
    const error = new ApiResponseError(422, [
      { loc: ['body', 'agent_model_id'], msg: 'agent_model_id cannot be blank' },
      { loc: ['body', 'target_count'], msg: 'must be less than or equal to 24' },
    ])

    expect(error.message).toBe(
      'body.agent_model_id：agent_model_id cannot be blank；body.target_count：must be less than or equal to 24（422）',
    )
  })
})
