<script setup lang="ts">
import { ref } from 'vue'
import {
  ArrowUpRight,
  Eye,
  EyeOff,
  KeyRound,
  LockKeyhole,
  UserRound,
} from 'lucide-vue-next'
import { useConsole } from '../store'
import { errorText } from '../api'
import WireArt from '../components/WireArt.vue'
const store = useConsole()
const username = ref(''),
  password = ref(''),
  message = ref(''),
  busy = ref(false),
  show = ref(false)
async function submit() {
  if (busy.value || !store.ready) return
  busy.value = true
  message.value = ''
  try {
    await store.login(username.value.trim(), password.value)
    password.value = ''
  } catch (e) {
    message.value = errorText(e)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div class="login">
    <section class="login-visual">
      <div class="brand">
        <span class="brand-mark"><i /><i /><i /></span
        ><span>PolyQuant <b>Pro</b></span>
      </div>
      <WireArt />
      <div class="login-caption">
        <small>POLYMARKET RESEARCH</small>
        <h1>量化研究终端<span>.</span></h1>
        <p>BTC / ETH <i /> 5m / 15m</p>
      </div>
    </section>
    <section class="login-form">
      <div class="login-box">
        <div class="login-icon"><KeyRound :size="22" /></div>
        <h2>登录工作区</h2>
        <p class="muted">使用研究团队分配的账户访问管理后台。</p>
        <form @submit.prevent="submit">
          <label
            >用户名
            <div class="field-icon">
              <UserRound :size="16" /><input
                v-model="username"
                name="username"
                maxlength="80"
                required
                autocomplete="username"
                placeholder="输入用户名"
              /></div
          ></label>
          <label
            >密码
            <div class="field-icon">
              <LockKeyhole :size="16" /><input
                v-model="password"
                name="password"
                aria-label="密码"
                maxlength="256"
                :type="show ? 'text' : 'password'"
                required
                autocomplete="current-password"
                placeholder="输入密码"
              /><button
                type="button"
                class="icon"
                :aria-label="show ? '隐藏密码' : '显示密码'"
                :aria-pressed="show"
                @click="show = !show"
              >
                <component :is="show ? EyeOff : Eye" :size="16" />
              </button></div
          ></label>
          <p v-if="message || store.serviceError" class="error" role="alert">
            {{ message || store.serviceError }}
          </p>
          <button class="primary" :disabled="busy || !store.ready">
            {{ busy ? '正在登录…' : '登录终端' }}<ArrowUpRight :size="17" />
          </button>
        </form>
      </div>
    </section>
  </div>
</template>
