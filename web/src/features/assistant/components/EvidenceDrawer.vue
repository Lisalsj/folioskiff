<script setup lang="ts">
import { researchText as t, serverMessage } from '@/stores/locale'
import { ref } from 'vue'
import { PhCaretDown, PhFingerprint } from '@phosphor-icons/vue'
import { CollapsibleContent, CollapsibleRoot, CollapsibleTrigger } from 'reka-ui'

import type { AssistantContext } from '../api'

defineProps<{
  currentScopeLabel: string
  context: AssistantContext | null
}>()

const open = ref(false)

function shortHash(value: string): string {
  return value.slice(0, 12)
}
</script>

<template>
  <CollapsibleRoot v-model:open="open" class="evidence-drawer">
    <CollapsibleTrigger data-test="assistant-evidence-trigger">
      <span>
        <PhFingerprint :size="15" aria-hidden="true" />
        {{ t('证据与复现信息') }}
      </span>
      <PhCaretDown :size="14" aria-hidden="true" />
    </CollapsibleTrigger>
    <CollapsibleContent class="evidence-content">
      <dl>
        <div>
          <dt>{{ t('当前选择') }}</dt>
          <dd>{{ currentScopeLabel }}</dd>
        </div>
        <template v-if="context">
          <div>
            <dt>{{ t('冻结页码') }}</dt>
            <dd>{{ context.page_numbers.join('、') || t('无页码证据') }}</dd>
          </div>
          <div>
            <dt>{{ t('文档哈希') }}</dt>
            <dd><code>{{ shortHash(context.document_content_sha256) }}</code></dd>
          </div>
          <div>
            <dt>{{ t('证据哈希') }}</dt>
            <dd><code>{{ shortHash(context.evidence_sha256) }}</code></dd>
          </div>
          <div>
            <dt>{{ t('快照哈希') }}</dt>
            <dd><code>{{ shortHash(context.snapshot_sha256) }}</code></dd>
          </div>
        </template>
      </dl>
      <ul v-if="context?.warnings.length" :aria-label="t('证据警告')">
        <li v-for="warning in context.warnings" :key="warning">{{ serverMessage(warning) }}</li>
      </ul>
      <p v-else-if="!context">{{ t('发送后会在此显示不可变文档与证据指纹。') }}</p>
    </CollapsibleContent>
  </CollapsibleRoot>
</template>

<style scoped>
.evidence-drawer {
  border-top: 1px solid var(--color-line);
}

.evidence-drawer > button {
  width: 100%;
  min-height: 38px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 13px;
  border: 0;
  background: transparent;
  color: var(--color-muted-strong);
  font-size: var(--type-meta);
  cursor: pointer;
}

.evidence-drawer > button > span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.evidence-drawer > button svg:last-child {
  transition: transform 140ms ease;
}

.evidence-drawer > button[aria-expanded='true'] svg:last-child {
  transform: rotate(180deg);
}

.evidence-drawer > button:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: -3px;
}

.evidence-content {
  padding: 0 13px 12px;
}

.evidence-content dl {
  display: grid;
  gap: 6px;
  margin: 0;
}

.evidence-content dl > div {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 8px;
  font-size: var(--type-meta);
}

.evidence-content dt {
  color: var(--color-muted);
}

.evidence-content dd {
  margin: 0;
  color: var(--color-muted-strong);
  overflow-wrap: anywhere;
}

.evidence-content code {
  font-family: 'Cascadia Mono', Consolas, monospace;
  color: var(--color-forest);
}

.evidence-content ul {
  display: grid;
  gap: 3px;
  margin: 9px 0 0;
  padding-left: 16px;
  color: var(--color-wine);
  font-size: var(--type-meta);
}

.evidence-content p {
  margin: 7px 0 0;
  color: var(--color-muted);
  font-size: var(--type-meta);
  line-height: 1.5;
}

@media (prefers-reduced-motion: reduce) {
  .evidence-drawer > button svg:last-child {
    transition: none;
  }
}
</style>
