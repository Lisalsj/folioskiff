<script setup lang="ts">
import { computed, watchEffect } from 'vue'
import { useRoute } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import { updatePageTitle } from '@/router'

const route = useRoute()
const isPublicSetup = computed(() => route.meta.publicSetup === true)
watchEffect(() => updatePageTitle(route.meta.title))
</script>

<template>
  <RouterView v-if="isPublicSetup" />
  <AppShell v-else>
    <RouterView />
  </AppShell>
</template>
