import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AssistantTimeline from './AssistantTimeline.vue'

describe('AssistantTimeline', () => {
  it('navigates local citations and exposes a failed task retry without external links', async () => {
    const wrapper = mount(AssistantTimeline, {
      props: {
        loading: false,
        messages: [{
          id: 'message-user',
          session_id: 'session-1',
          task_id: 'task-1',
          sequence: 1,
          role: 'user',
          status: 'committed',
          content: '第二页提供了什么证据？',
          citations: [],
          evidence_manifest: { document_content_sha256: 'a'.repeat(64) },
          job_id: 'job-1',
          reply_to_message_id: null,
          created_at: '2026-08-01T00:00:00Z',
        }, {
          id: 'message-1',
          session_id: 'session-1',
          task_id: 'task-1',
          sequence: 2,
          role: 'assistant',
          status: 'committed',
          content: '这个结论由第二页的实验支持。',
          citations: [{ evidence_id: 'page:2:native', kind: 'page', page_number: 2 }],
          evidence_manifest: { document_content_sha256: 'a'.repeat(64) },
          job_id: 'job-1',
          reply_to_message_id: null,
          created_at: '2026-08-01T00:00:00Z',
        }],
        tasks: [{
          id: 'task-2',
          session_id: 'session-1',
          retry_of_task_id: null,
          mode: 'explain',
          status: 'failed',
          network_policy: 'local_only',
          backend_id: 'codex_cli',
          requested_model_id: null,
          reasoning_effort: 'medium',
          service_tier: 'flex',
          output_language: 'zh-CN',
          job_id: 'job-2',
          error_code: 'backend_unavailable',
          diagnostic: '后端暂时不可用',
          retryable: true,
          context: {
            id: 'snapshot-2',
            work_id: 'work-1',
            document_version_id: 'document-1',
            document_content_sha256: 'a'.repeat(64),
            scope: 'current_section',
            page_numbers: [2],
            selection: null,
            annotation_ids: [],
            section_fallback: true,
            extraction_version: 'fixture',
            warnings: ['section_fallback'],
            evidence_manifest: {},
            evidence_sha256: 'b'.repeat(64),
            snapshot_sha256: 'c'.repeat(64),
            created_at: '2026-08-01T00:00:00Z',
          },
          latest_run: null,
          created_at: '2026-08-01T00:00:00Z',
          started_at: '2026-08-01T00:00:00Z',
          completed_at: '2026-08-01T00:01:00Z',
          updated_at: '2026-08-01T00:01:00Z',
        }],
        fragments: [],
        activeTaskId: '',
        currentDocumentContentSha256: 'a'.repeat(64),
        latestCheckpoint: null,
      },
    })

    expect(wrapper.find('a').exists()).toBe(false)
    expect(wrapper.get('.timeline-message.user').text()).toContain('第二页提供了什么证据')
    await wrapper.get('[data-test="assistant-citation"]').trigger('click')
    expect(wrapper.emitted('navigatePage')?.at(-1)).toEqual([2])
    expect(wrapper.text()).toContain('章节范围已回退到当前页')
    await wrapper.get('[data-test="assistant-retry-task-2"]').trigger('click')
    expect(wrapper.emitted('retry')?.at(-1)).toEqual(['task-2'])
  })
})
