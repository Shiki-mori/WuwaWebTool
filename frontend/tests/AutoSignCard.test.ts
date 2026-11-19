import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'
import AutoSignCard from '@/components/index/AutoSignCard.vue'
import { apiAutoSignDisable, apiAutoSignEnable, apiAutoSignStatus, apiAutoSignSync } from '@/composables/useApi'
import type { AutoSignResponse, AutoSignStatus } from '@/types/autosign'

let auth = reactive({ uid: '123456789', token: 'test-token', did: 'test-device' })
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ auth, loggedIn: !!auth.token }) }))
vi.mock('@/composables/useApi', () => ({
  apiAutoSignStatus: vi.fn(), apiAutoSignEnable: vi.fn(), apiAutoSignSync: vi.fn(), apiAutoSignDisable: vi.fn(),
}))
const status = vi.mocked(apiAutoSignStatus)
const enable = vi.mocked(apiAutoSignEnable)
const sync = vi.mocked(apiAutoSignSync)
const disable = vi.mocked(apiAutoSignDisable)
let wrapper: VueWrapper | undefined

const state = (o: Partial<AutoSignStatus> = {}): AutoSignStatus => ({
  supported: true, enabled: false, uid: null, time: null, task_installed: false,
  task_time: null, last_run: null, msg: '', ...o,
})
const resp = (s: Partial<AutoSignStatus> = {}, o: Partial<AutoSignResponse> = {}): AutoSignResponse => ({
  success: true, msg: '', status: state(s), ...o,
})
const on = { enabled: true, uid: '123456789', time: '08:15', task_installed: true, task_time: '08:15' }

beforeEach(() => {
  ;[status, enable, sync, disable].forEach((m) => m.mockReset())
  auth = reactive({ uid: '123456789', token: 'test-token', did: 'test-device' })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.restoreAllMocks() })

describe('daily auto sign-in card', () => {
  it('shows disabled state and enables with the chosen time', async () => {
    status.mockResolvedValue(resp())
    enable.mockResolvedValue(resp(on, { msg: '已开启，每天 08:15 自动签到' }))
    wrapper = mount(AutoSignCard)
    await flushPromises()
    expect(sync).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('未开启')
    await wrapper.find('input[type=time]').setValue('08:15')
    await wrapper.find('button.autosign-save').trigger('click')
    await flushPromises()
    expect(enable).toHaveBeenCalledWith('08:15')
    expect(wrapper.text()).toContain('已开启 · 每天 08:15')
    expect(wrapper.find('button.autosign-off').exists()).toBe(true)
  })

  it('syncs fresh login for the same uid and shows the last run', async () => {
    status.mockResolvedValue(resp(on))
    sync.mockResolvedValue(resp({ ...on, last_run: {
      time: '2026-11-19T08:15:03+08:00', date: '2026-11-19', uid: '123456789', attempts: 1,
      success: true, status: 'signed', msg: '鸣潮游戏奖励签到成功', signed_days: 19, rewards: [],
    } }))
    wrapper = mount(AutoSignCard)
    await flushPromises()
    expect(sync).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('2026-11-19 08:15：鸣潮游戏奖励签到成功，累计 19 天')
  })

  it('does not sync another account and explains the binding', async () => {
    status.mockResolvedValue(resp({ ...on, uid: '987654321' }))
    wrapper = mount(AutoSignCard)
    await flushPromises()
    expect(sync).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('当前页面登录的是 UID 123456789')
  })

  it('warns when the scheduled task is missing or drifted', async () => {
    status.mockResolvedValue(resp({ ...on, task_installed: false, task_time: null }))
    sync.mockImplementation(() => status.mock.results[0].value)
    wrapper = mount(AutoSignCard)
    await flushPromises()
    expect(wrapper.text()).toContain('未找到 Windows 计划任务')
  })

  it('surfaces enable failures and local-only refusal', async () => {
    status.mockResolvedValueOnce(resp())
    enable.mockResolvedValue(resp({}, { success: false, msg: '未开启：登录已过期，请重新登录' }))
    wrapper = mount(AutoSignCard)
    await flushPromises()
    await wrapper.find('button.autosign-save').trigger('click')
    await flushPromises()
    expect(wrapper.find('.is-error').text()).toContain('登录已过期')
    wrapper.unmount()
    status.mockRejectedValueOnce(new Error('forbidden'))
    wrapper = mount(AutoSignCard)
    await flushPromises()
    expect(wrapper.text()).toContain('只能在运行本服务的电脑上')
  })

  it('asks before disabling', async () => {
    status.mockResolvedValue(resp(on))
    sync.mockResolvedValue(resp(on))
    disable.mockResolvedValue(resp({}, { msg: '已关闭自动签到' }))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    wrapper = mount(AutoSignCard)
    await flushPromises()
    await wrapper.find('button.autosign-off').trigger('click')
    expect(disable).not.toHaveBeenCalled()
    await wrapper.find('button.autosign-off').trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalledTimes(2)
    expect(disable).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('未开启')
  })

  it('hides itself on unsupported platforms and drops stale responses on account switch', async () => {
    let resolveFirst!: (r: AutoSignResponse) => void
    status.mockReturnValueOnce(new Promise((r) => { resolveFirst = r }))
      .mockResolvedValueOnce(resp({ supported: false, msg: '仅支持 Windows' }))
    wrapper = mount(AutoSignCard)
    auth.uid = '555555555'
    await nextTick()
    await flushPromises()
    resolveFirst(resp(on))
    await flushPromises()
    expect(sync).not.toHaveBeenCalled()
    expect(wrapper.find('section').exists()).toBe(false)
  })
})
