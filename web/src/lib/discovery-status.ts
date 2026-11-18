import { discoveryText as t } from '@/stores/locale'
import type { components } from '@/api/schema'

type RunBatch = components['schemas']['RunBatchResponse']

export function discoveryRunStatusLabel(status: string): string {
  return {
    queued: t('等待运行'),
    running: t('正在运行'),
    succeeded: t('运行成功'),
    partial: t('部分成功'),
    partial_success: t('部分成功'),
    failed: t('运行失败'),
    cancelled: t('已取消'),
  }[status] ?? status
}

export function discoveryBatchStatusLabel(batch: RunBatch): string {
  if (batch.status === 'failed' && batch.retryable) return t('待重试')
  return {
    pending: t('等待中'),
    queued: t('等待中'),
    running: t('运行中'),
    succeeded: t('成功'),
    failed: t('失败'),
    cancelled: t('已取消'),
  }[batch.status] ?? batch.status
}

export function discoveryBatchErrorLabel(batch: RunBatch): string {
  if (batch.status !== 'failed') return ''
  return {
    permanent_request: t('来源拒绝检索参数；更新后请重新运行计划。'),
    authentication: t('来源认证失败，请检查来源设置。'),
    rate_limited: t('来源限制请求频率，请稍后重试失败来源。'),
    remote_5xx: t('来源服务暂时出错，请重试失败来源。'),
    timeout: t('连接来源超时，请重试失败来源。'),
    transport: t('无法连接来源，请检查网络后重试。'),
    temporary_network: t('来源连接超时或中断，请检查网络后重试失败来源。'),
    response_incompatible: t('来源响应格式不兼容，请检查应用更新。'),
    connector_not_installed: t('此来源未安装，请检查来源设置。'),
    connector_disabled: t('此来源已停用，请检查来源设置。'),
    missing_api_key: t('此来源需要密钥，请检查来源设置。'),
    connector_internal_error: t('来源处理出错，请检查应用更新。'),
  }[batch.error_code ?? ''] ?? batch.error_message ?? t('此来源检索失败。')
}

export function discoveryStopReasonLabel(batch: RunBatch): string {
  return {
    scan_limit: t('已到扫描上限，结果可能不完整'),
    new_work_quota: t('已达到本次新论文配额'),
    source_exhausted: t('已扫描完来源返回的结果'),
  }[String(batch.checkpoint.stop_reason ?? '')] ?? ''
}

export function recommendationDegradationLabel(reason: string): string {
  return {
    insufficient_eligible_candidates: t('符合推荐条件的候选不足'),
    semantic_index_unavailable: t('语义索引暂不可用，已使用词法检索'),
    candidate_pool_capped: t('已达到本次候选配额'),
    exploration_candidates_unavailable: t('暂无符合探索条件的候选'),
    exploration_quota_partial: t('探索候选不足，已保留可用结果'),
    integrity_evidence_incomplete: t('论文完整性证据尚不完整'),
  }[reason] ?? reason
}
