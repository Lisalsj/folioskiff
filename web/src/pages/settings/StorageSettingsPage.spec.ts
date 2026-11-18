import { shallowMount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import BackupSettings from '@/components/BackupSettings.vue'
import ModelAssetSettings from '@/components/ModelAssetSettings.vue'
import ReaderStorageSettings from '@/components/ReaderStorageSettings.vue'

import StorageSettingsPage from './StorageSettingsPage.vue'

describe('StorageSettingsPage', () => {
  it('groups model, reader cache, and backup controls in one focused section', () => {
    const wrapper = shallowMount(StorageSettingsPage)
    expect(wrapper.findComponent(ModelAssetSettings).exists()).toBe(true)
    expect(wrapper.findComponent(ReaderStorageSettings).exists()).toBe(true)
    expect(wrapper.findComponent(BackupSettings).exists()).toBe(true)
  })
})
