<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { apiAutoSignDisable, apiAutoSignEnable, apiAutoSignStatus, apiAutoSignSync } from '@/composables/useApi'
import { useAuthStore } from '@/stores/auth'
import type { AutoSignResponse, AutoSignStatus } from '@/types/autosign'

const authStore = useAuthStore()
const status = ref<AutoSignStatus | null>(null)
const time = ref('09:00')
const busy = ref(false)
const message = ref('')
const isError = ref(false)
let generation = 0

const canUse = computed(() => authStore.loggedIn && !!authStore.auth.uid)
const enabled = computed(() => status.value?.enabled === true)
const otherUid = computed(() => enabled.value && !!status.value?.uid && status.value.uid !== authStore.auth.uid)
const taskWarning = computed(() => {
  const s = status.value
  if (!s?.enabled || !s.uid) return ''
  if (!s.task_installed) return '未找到 Windows 计划任务，请点击“保存设置”重新创建'
  if (s.task_time && s.time && s.task_time !== s.time) return `计划任务实际时间为 ${s.task_time}，与设置不一致，请重新保存`
  return ''
})
const lastRun = computed(() => status.value?.last_run ?? null)
const lastRunText = computed(() => {
  const r = lastRun.value
  if (!r) return ''
  const when = r.time.replace('T', ' ').slice(0, 16)
  const days = r.signed_days != null ? `，累计 ${r.signed_days} 天` : ''
  return `${when}：${r.msg}${days}`
})

function apply(r: AutoSignResponse, gen: number, showMsg = true) {
  if (gen !== generation) return
  status.value = r.status
  if (r.status.time) time.value = r.status.time
  if (showMsg && r.msg) {
    message.value = r.msg
    isError.value = !r.success
  } else if (!r.success) {
    message.value = r.msg || r.status.msg
    isError.value = true
  }
}

function fail(e: unknown, gen: number) {
  if (gen !== generation) return
  isError.value = true
  message.value = e instanceof Error && e.message === 'forbidden'
    ? '自动签到只能在运行本服务的电脑上，通过 localhost 打开页面设置'
    : '自动签到服务暂时无法访问，请稍后刷新页面'
}

async function load() {
  const gen = ++generation
  status.value = null
  message.value = ''
  isError.value = false
  if (!canUse.value) return
  busy.value = true
  try {
    let r = await apiAutoSignStatus()
    if (gen !== generation) return
    // Same account logged in again with a new token: keep the saved credentials current.
    if (r.status.enabled && r.status.uid === authStore.auth.uid) r = await apiAutoSignSync()
    apply(r, gen)
  } catch (e) {
    fail(e, gen)
  } finally {
    if (gen === generation) busy.value = false
  }
}

async function act(call: () => Promise<AutoSignResponse>) {
  if (busy.value) return
  const gen = generation
  busy.value = true
  message.value = ''
  try {
    apply(await call(), gen)
  } catch (e) {
    fail(e, gen)
  } finally {
    if (gen === generation) busy.value = false
  }
}

const enable = () => act(() => apiAutoSignEnable(time.value))
function disable() {
  if (!window.confirm('关闭自动签到会删除 Windows 计划任务和本机保存的登录信息，确定吗？')) return
  void act(apiAutoSignDisable)
}

watch(() => [authStore.auth.uid, authStore.auth.token, authStore.auth.did], () => void load(), { immediate: true })
</script>

<template>
  <section v-if="canUse && status?.supported !== false" class="section-container page-sidebar-section autosign-card"
           aria-label="每日自动签到" :aria-busy="busy">
    <div class="section-header">
      <span class="section-title">每日自动签到</span>
      <div class="section-deco-line"></div>
    </div>

    <template v-if="status">
      <div class="autosign-state" role="status">
        <span class="autosign-dot" :class="{ 'is-on': enabled && !taskWarning }"></span>
        <span v-if="enabled">已开启 · 每天 {{ status.time || '--:--' }}<template v-if="status.uid"> · UID {{ status.uid }}</template></span>
        <span v-else>未开启</span>
      </div>
      <p v-if="taskWarning" class="autosign-message is-error">{{ taskWarning }}</p>
      <p v-if="otherUid" class="autosign-message">
        当前页面登录的是 UID {{ authStore.auth.uid }}。保存设置会改为当前账号；切换回原账号则自动同步登录信息。
      </p>
      <p v-if="lastRun" class="autosign-message" :class="{ 'is-error': !lastRun.success }">上次运行 {{ lastRunText }}</p>
      <p v-if="lastRun?.status === 'login_expired'" class="autosign-message">重新登录库街区后，打开本页即会自动同步新的登录信息。</p>
    </template>
    <p v-if="message" class="autosign-message" :class="{ 'is-error': isError }" aria-live="polite">{{ message }}</p>

    <div v-if="status" class="autosign-actions">
      <label class="autosign-time">
        <span>时间</span>
        <input v-model="time" type="time" required :disabled="busy" />
      </label>
      <button type="button" class="autosign-save" :disabled="busy || !time" @click="enable">
        {{ busy ? '处理中…' : enabled ? '保存设置' : '开启自动签到' }}
      </button>
      <button v-if="enabled" type="button" class="autosign-off" :disabled="busy" @click="disable">关闭</button>
    </div>
    <p class="autosign-note">
      由 Windows 任务计划程序每天运行，网页和服务无需开着；错过时间会在开机后补签。
      登录信息经 Windows 加密只保存在本机，复用当前网页登录，无需再次登录。
    </p>
  </section>
</template>

<style scoped>
.autosign-card { padding: 16px; }
.autosign-state { display: flex; align-items: center; gap: 8px; margin: 14px 0 8px; color: #e8edf2; font-size: 13px; overflow-wrap: anywhere; }
.autosign-dot { flex: none; width: 7px; height: 7px; border-radius: 50%; background: #9aa0aa; }
.autosign-dot.is-on { background: #80c7a3; }
.autosign-message { font-size: 12px; color: #9aa0aa; line-height: 1.7; overflow-wrap: anywhere; }
.autosign-message.is-error { color: #f2a3a3; }
.autosign-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 12px; }
.autosign-time { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #9aa0aa; }
.autosign-time input { background: transparent; border: 1px solid var(--color-line); border-radius: 6px; color: #e8edf2; padding: 6px 8px; font-size: 12px; color-scheme: dark; }
.autosign-actions button { border-radius: 6px; padding: 8px 10px; font-size: 12px; cursor: pointer; }
.autosign-actions button:disabled { opacity: .5; cursor: default; }
.autosign-save { background: transparent; border: 1px solid #d4b163; color: #d4b163; flex: 1; }
.autosign-off { background: transparent; border: 1px solid var(--color-line); color: #9aa0aa; }
.autosign-note { font-size: 10px; color: #9aa0aa; line-height: 1.6; margin-top: 12px; }
</style>
