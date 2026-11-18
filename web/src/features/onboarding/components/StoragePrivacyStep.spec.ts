import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import StoragePrivacyStep from './StoragePrivacyStep.vue'

const writableStorage = {
  writable: true,
  state_writable: true,
  cache_writable: true,
  library_writable: true,
  state_root: 'C:\\Users\\Researcher\\AppData\\Local\\paper-reader',
  cache_root: 'C:\\Users\\Researcher\\AppData\\Local\\paper-reader-cache',
  library_root: 'D:\\Papers\\paper-reader',
  backups_root: 'D:\\Papers\\paper-reader\\backups',
}

describe('StoragePrivacyStep', () => {
  it('requires explicit privacy acknowledgement and cannot be skipped', async () => {
    const wrapper = mount(StoragePrivacyStep, { props: { capability: writableStorage } })

    expect(wrapper.find('[data-test="skip-step"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="confirm-storage"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[aria-label="应用状态目录"]').text()).toBe(writableStorage.state_root)
    expect(wrapper.get('[aria-label="备份目录"]').text()).toBe(writableStorage.backups_root)
    expect(wrapper.find('input[aria-label="应用状态目录"]').exists()).toBe(false)
    expect(wrapper.find('input[aria-label="备份目录"]').exists()).toBe(false)

    await wrapper.get('[data-test="privacy-acknowledgement"]').setValue(true)
    await wrapper.get('[data-test="confirm-storage"]').trigger('submit')

    expect(wrapper.emitted('complete')?.[0]).toEqual([{
      cache_root: writableStorage.cache_root,
      library_root: writableStorage.library_root,
      privacy_acknowledged: true,
    }])
  })

  it('rejects relative Windows paths before emitting', async () => {
    const wrapper = mount(StoragePrivacyStep, { props: { capability: writableStorage } })
    await wrapper.get('[data-test="privacy-acknowledgement"]').setValue(true)
    await wrapper.get('[data-test="library-root"]').setValue('papers')

    expect(wrapper.get('[data-test="confirm-storage"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[role="alert"]').text()).toContain('绝对路径')
    expect(wrapper.emitted('complete')).toBeUndefined()
  })

  it('requires the exact phrase before requesting a destructive reset', async () => {
    const wrapper = mount(StoragePrivacyStep, {
      props: { capability: writableStorage, resetRequired: true },
    })
    const confirmation = wrapper.get<HTMLInputElement>('[data-test="reset-confirmation"]')
    await confirmation.setValue('REBUILD PAPER-READER DATA')
    await wrapper.get('[data-test="request-reset"]').trigger('click')

    expect(wrapper.emitted('requestReset')?.[0]).toEqual(['REBUILD PAPER-READER DATA'])
    expect(confirmation.element.value).toBe('')
  })
})
