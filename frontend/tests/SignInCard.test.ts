import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'
import SignInCard from '@/components/index/SignInCard.vue'
import { apiGameSignIn } from '@/composables/useApi'
import type { SignInResult } from '@/types/signin'

let auth = reactive({ uid: '123456789', token: 'test-token', did: 'test-device' })
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ auth, loggedIn: !!auth.token }) }))
vi.mock('@/composables/useApi', () => ({ apiGameSignIn: vi.fn() }))
const api = vi.mocked(apiGameSignIn)
let wrapper: VueWrapper | undefined
const state = (overrides: Partial<SignInResult> = {}): SignInResult => ({
  success: true, status: 'unsigned', msg: '今日未签到', signed_today: false,
  signed_days: 19, checked_date: '2026-10-24', rewards: [], ...overrides,
})
const submit = () => wrapper!.find('button.signin-submit')
const refresh = () => wrapper!.find('button.signin-refresh')

beforeEach(() => {
  api.mockReset()
  auth = reactive({ uid: '123456789', token: 'test-token', did: 'test-device' })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.useRealTimers() })

describe('manual Wuwa sign-in', () => {
  it('only reads status on mount and renders an unsigned account', async () => {
    api.mockResolvedValue(state())
    wrapper = mount(SignInCard)
    await flushPromises()
    expect(api.mock.calls).toEqual([[false]])
    expect(submit().attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('19 天')
    expect(wrapper.find('ul').exists()).toBe(false)
  })

  it('submits once while busy and displays only returned rewards', async () => {
    let resolve!: (value: SignInResult) => void
    api.mockResolvedValueOnce(state()).mockReturnValueOnce(new Promise((r) => { resolve = r }))
    wrapper = mount(SignInCard)
    await flushPromises()
    await submit().trigger('click')
    await submit().trigger('click')
    expect(api.mock.calls).toEqual([[false], [true]])
    expect(submit().attributes('disabled')).toBeDefined()
    resolve(state({ status: 'signed', signed_today: true, msg: '签到成功', rewards: [{ name: '星声', count: 20 }] }))
    await flushPromises()
    expect(wrapper.text()).toContain('签到成功')
    expect(wrapper.text()).toContain('星声 × 20')
    expect(submit().attributes('disabled')).toBeDefined()
  })

  it('does not write for an already signed account', async () => {
    api.mockResolvedValue(state({ signed_today: true, status: 'already_signed', msg: '今日已签到' }))
    wrapper = mount(SignInCard)
    await flushPromises()
    await submit().trigger('click')
    expect(api).toHaveBeenCalledTimes(1)
    expect(submit().attributes('disabled')).toBeDefined()
  })

  it('shows a login link for expired credentials without clearing stored auth', async () => {
    api.mockResolvedValue(state({ success: false, signed_today: null, status: 'login_expired', msg: '登录已过期，请重新登录' }))
    wrapper = mount(SignInCard)
    await flushPromises()
    expect(wrapper.find('a').attributes('href')).toBe('/login')
    expect(submit().attributes('disabled')).toBeDefined()
    expect(auth.token).toBe('test-token')
  })

  it('clears unsigned state on a write timeout and requires a status refresh', async () => {
    api.mockResolvedValueOnce(state()).mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(state({ signed_today: true, status: 'already_signed' }))
    wrapper = mount(SignInCard)
    await flushPromises()
    await submit().trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('签到结果暂未确认')
    expect(submit().attributes('disabled')).toBeDefined()
    expect(refresh().attributes('disabled')).toBeUndefined()
    await refresh().trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('今日已签到')
    expect(api.mock.calls).toEqual([[false], [true], [false]])
  })

  it('ignores a late response from the previous account', async () => {
    let resolve!: (value: SignInResult) => void
    api.mockReturnValueOnce(new Promise((r) => { resolve = r })).mockResolvedValueOnce(state())
    wrapper = mount(SignInCard)
    auth.uid = '123456790'
    await nextTick()
    await flushPromises()
    resolve(state({ signed_today: true, status: 'already_signed', msg: 'previous account' }))
    await flushPromises()
    expect(wrapper.text()).not.toContain('previous account')
    expect(submit().attributes('disabled')).toBeUndefined()
  })

  it('refreshes after midnight in Beijing', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-24T15:59:45Z'))
    api.mockResolvedValueOnce(state({ signed_today: true, status: 'already_signed' })).mockResolvedValueOnce(state())
    wrapper = mount(SignInCard)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30_000)
    await flushPromises()
    expect(api.mock.calls).toEqual([[false], [false]])
    expect(submit().attributes('disabled')).toBeUndefined()
  })

  it('keeps unknown errors disabled and escapes upstream text', async () => {
    api.mockResolvedValue(state({ success: false, signed_today: null, status: 'error', msg: '<img src=x onerror=alert(1)>' }))
    wrapper = mount(SignInCard)
    await flushPromises()
    expect(wrapper.find('img').exists()).toBe(false)
    expect(submit().attributes('disabled')).toBeDefined()
  })

  it('ignores yesterday\'s in-flight response when the day changes', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-24T15:59:45Z'))
    let resolve!: (value: SignInResult) => void
    api.mockReturnValueOnce(new Promise((r) => { resolve = r })).mockResolvedValueOnce(state())
    wrapper = mount(SignInCard)
    await vi.advanceTimersByTimeAsync(30_000)
    await flushPromises()
    resolve(state({ signed_today: true, status: 'already_signed', msg: 'yesterday' }))
    await flushPromises()
    expect(wrapper.text()).not.toContain('yesterday')
    expect(submit().attributes('disabled')).toBeUndefined()
  })
})
