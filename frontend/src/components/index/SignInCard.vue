<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { apiGameSignIn } from '@/composables/useApi'
import { useAuthStore } from '@/stores/auth'
import type { SignInResult } from '@/types/signin'

const authStore = useAuthStore()
const result = ref<SignInResult | null>(null)
const busy = ref(false)
const submitting = ref(false)
const error = ref('')
let generation = 0
let timer: ReturnType<typeof setInterval> | undefined
const canCheck = computed(() => authStore.loggedIn && !!authStore.auth.uid)
const signed = computed(() => result.value?.signed_today === true)
const canSign = computed(() => result.value?.success && result.value.signed_today === false)
const cnDate = () => new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
}).format(new Date())
let day = cnDate()

async function refresh(submit = false) {
  if (!canCheck.value || busy.value) return
  const requestGeneration = ++generation
  const requestDate = cnDate()
  busy.value = true
  submitting.value = submit
  error.value = ''
  // Unconfirmed write outcomes must not leave a stale unsigned state with an enabled button.
  if (submit) result.value = null
  try {
    const response = await apiGameSignIn(submit)
    if (requestGeneration !== generation) return
    if (requestDate !== cnDate()) {
      result.value = null
      error.value = '日期已更新，请刷新今日签到状态'
      return
    }
    result.value = response
  } catch {
    if (requestGeneration !== generation) return
    result.value = null
    error.value = submit
      ? '签到结果暂未确认，请先刷新状态再决定是否重试'
      : '网络异常，暂时无法查询签到状态，请刷新重试'
  } finally {
    if (requestGeneration === generation) {
      busy.value = false
      submitting.value = false
    }
  }
}

watch(() => [authStore.auth.uid, authStore.auth.token, authStore.auth.did], () => {
  ++generation
  result.value = null
  error.value = ''
  busy.value = false
  submitting.value = false
  void refresh()
}, { immediate: true })

function checkDate() {
  if (document.hidden) return
  const today = cnDate()
  if (today !== day) {
    ++generation
    day = today
    result.value = null
    error.value = ''
    busy.value = false
    submitting.value = false
    void refresh()
  }
}

onMounted(() => {
  timer = setInterval(checkDate, 30_000)
  document.addEventListener('visibilitychange', checkDate)
})
onUnmounted(() => {
  ++generation
  clearInterval(timer)
  document.removeEventListener('visibilitychange', checkDate)
})
</script>

<template>
  <section class="section-container page-sidebar-section signin-card" aria-label="鸣潮游戏签到" :aria-busy="busy">
    <div class="section-header">
      <span class="section-title">鸣潮游戏签到</span>
      <div class="section-deco-line"></div>
    </div>
    <div class="signin-state" aria-live="polite" role="status">
      <span class="signin-dot" :class="{ 'is-signed': signed }"></span>
      <span>{{ busy ? (submitting ? '正在签到…' : '正在查询…') : signed ? '今日已签到' : canSign ? '今日未签到' : '状态待确认' }}</span>
    </div>
    <p v-if="!canCheck" class="signin-message">请先登录并选择鸣潮角色</p>
    <p v-else-if="error || result?.msg" class="signin-message" :class="{ 'is-error': error || result?.success === false }" aria-live="polite">
      {{ error || result?.msg }}
    </p>
    <p v-if="result?.signed_days != null" class="signin-message">本期累计签到 {{ result.signed_days }} 天</p>
    <ul v-if="result?.rewards?.length" class="signin-rewards" aria-label="本次签到奖励">
      <li v-for="(reward, index) in result.rewards" :key="index">
        {{ reward.name }}<template v-if="reward.count != null"> × {{ reward.count }}</template>
      </li>
    </ul>
    <div class="signin-actions">
      <button type="button" class="signin-submit" :disabled="busy || !canSign" @click="refresh(true)">
        {{ submitting ? '正在签到…' : signed ? '今日已签到' : '领取游戏签到奖励' }}
      </button>
      <button type="button" class="signin-refresh" :disabled="busy || !canCheck" @click="refresh()">刷新状态</button>
    </div>
    <a v-if="result?.status === 'login_expired'" href="/login" class="signin-login">重新登录库街区</a>
    <p class="signin-note">国服 · 北京时间每日更新 · 手动领取</p>
  </section>
</template>

<style scoped>
.signin-card { padding: 16px; }
.signin-state { display: flex; align-items: center; gap: 8px; margin: 14px 0 8px; color: #e8edf2; font-size: 14px; }
.signin-dot { width: 7px; height: 7px; border-radius: 50%; background: #9aa0aa; }
.signin-dot.is-signed { background: #80c7a3; }
.signin-message { font-size: 12px; color: #9aa0aa; line-height: 1.7; overflow-wrap: anywhere; }
.signin-message.is-error { color: #f2a3a3; }
.signin-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.signin-actions button { border-radius: 6px; padding: 8px 10px; font-size: 12px; cursor: pointer; }
.signin-actions button:disabled { opacity: .5; cursor: default; }
.signin-submit { background: #d4b163; border: 1px solid #d4b163; color: #0f1115; font-weight: 600; flex: 1; }
.signin-refresh { background: transparent; border: 1px solid var(--color-line); color: #d4b163; }
.signin-note { font-size: 10px; color: #9aa0aa; margin-top: 12px; }
.signin-rewards { font-size: 12px; color: #d4b163; margin: 8px 0; padding-left: 16px; }
.signin-login { display: inline-block; color: #d4b163; font-size: 12px; margin-top: 10px; }
</style>
