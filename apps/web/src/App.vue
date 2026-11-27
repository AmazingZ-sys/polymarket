<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  darkTheme,
} from 'naive-ui'
import {
  Activity,
  ChevronDown,
  ChevronRight,
  LogOut,
  Menu,
  Monitor,
  Moon,
  Sun,
  X,
} from 'lucide-vue-next'
import Login from './views/Login.vue'
import { useConsole } from './store'
import { groups, byKey } from './nav'
import { errorText } from './api'
const store = useConsole(),
  route = useRoute(),
  mobile = ref(false),
  logoutError = ref(''),
  clock = ref(new Date())
const collapsed = ref(new Set<string>(['系统管理']))
const page = computed(() => byKey[String(route.name)]),
  group = computed(
    () =>
      groups.find((g) => g.items.some((p) => p.key === route.name))?.name ||
      '研究工作区',
  )
const time = computed(() =>
  clock.value.toLocaleTimeString('zh-CN', {
    hour12: false,
    timeZone: store.zone,
  }),
)
let timer: ReturnType<typeof setInterval>
function ended() {
  store.setIdentity(null)
}
function toggle(name: string) {
  const copy = new Set(collapsed.value)
  if (copy.has(name)) copy.delete(name)
  else copy.add(name)
  collapsed.value = copy
}
async function logout() {
  try {
    await store.logout()
  } catch (e) {
    logoutError.value = errorText(e)
  }
}
watch(
  () => store.theme,
  (t) => (document.documentElement.dataset.theme = t),
  { immediate: true },
)
watch(
  () => route.path,
  () => {
    mobile.value = false
    collapsed.value.delete(group.value)
    document.title = (page.value?.label || '研究终端') + ' · PolyQuant Pro'
  },
)
onMounted(() => {
  void store.initialize()
  window.addEventListener('session-ended', ended)
  timer = setInterval(() => (clock.value = new Date()), 1000)
})
onUnmounted(() => {
  clearInterval(timer)
  window.removeEventListener('session-ended', ended)
})
const overrides = {
  common: {
    primaryColor: '#9addad',
    primaryColorHover: '#aeeac0',
    primaryColorPressed: '#7cbb90',
    borderRadius: '6px',
  },
}
</script>
<template>
  <NConfigProvider
    :theme="store.theme === 'dark' ? darkTheme : null"
    :theme-overrides="overrides"
    ><NDialogProvider
      ><NMessageProvider>
        <Login v-if="!store.identity" />
        <div v-else class="shell">
          <button
            v-if="mobile"
            class="scrim"
            aria-label="关闭导航"
            @click="mobile = false"
          />
          <aside :class="{ mobile }">
            <div class="brand">
              <span class="brand-mark"><i /><i /><i /></span
              ><span>PolyQuant <b>Pro</b></span
              ><button
                class="icon mobile-only"
                aria-label="关闭导航"
                @click="mobile = false"
              >
                <X :size="18" />
              </button>
            </div>
            <nav aria-label="研究导航">
              <div v-for="g in groups" :key="g.name" class="nav-group">
                <button
                  class="group-toggle"
                  :aria-expanded="!collapsed.has(g.name)"
                  @click="toggle(g.name)"
                >
                  {{ g.name
                  }}<component
                    :is="collapsed.has(g.name) ? ChevronRight : ChevronDown"
                    :size="12"
                  />
                </button>
                <div v-show="!collapsed.has(g.name)">
                  <router-link
                    v-for="p in g.items.filter((item) => !('adminOnly' in item && item.adminOnly) || store.canWrite)"
                    :key="p.key"
                    :to="'/' + p.key"
                    :class="{
                      active:
                        route.name === p.key ||
                        (p.key === 'paper-accounts' &&
                          route.name === 'paper-account') ||
                        (p.key === 'markets' && route.name === 'market'),
                    }"
                    ><component :is="p.icon" :size="16" />{{
                      p.label
                    }}</router-link
                  >
                </div>
              </div>
            </nav>
            <footer class="userbox">
              <div class="avatar">
                {{ store.identity.username.slice(0, 1).toUpperCase() }}
              </div>
              <div>
                <strong>{{ store.identity.username }}</strong
                ><small>{{ store.canWrite ? '管理员' : '只读研究员' }}</small>
              </div>
              <button class="icon" aria-label="退出登录" @click="logout">
                <LogOut :size="16" />
              </button>
            </footer>
          </aside>
          <main>
            <header class="topbar">
              <button
                class="icon mobile-only"
                aria-label="打开导航"
                @click="mobile = true"
              >
                <Menu :size="18" /></button
              ><span class="crumb"
                ><Monitor :size="14" />研究终端<span>/</span>{{ group }}</span
              >
              <div class="top-tools">
                <span class="connection"
                  ><i :class="{ online: store.connection === 'ONLINE' }" />{{
                    store.connection === 'ONLINE'
                      ? '推送已连接'
                      : store.connection === 'RECONNECTING'
                        ? '推送重连中'
                        : '正在连接'
                  }}</span
                ><span class="clock"
                  >{{ time }} <span>{{ store.zone }}</span></span
                ><button
                  class="icon"
                  aria-label="切换主题"
                  @click="
                    store.setTheme(store.theme === 'dark' ? 'light' : 'dark')
                  "
                >
                  <component
                    :is="store.theme === 'dark' ? Sun : Moon"
                    :size="16"
                  />
                </button>
              </div>
            </header>
            <div class="content">
              <p v-if="logoutError" role="alert" class="error">
                {{ logoutError }}
              </p>
              <router-view
                :key="String(route.name) + '/' + (route.params.id ?? '')"
              />
            </div>
            <footer class="main-footer">
              <span><Activity :size="12" /> PolyQuant Pro</span
              ><span>虚拟资金研究环境</span>
            </footer>
          </main>
        </div>
      </NMessageProvider></NDialogProvider
    ></NConfigProvider
  >
</template>
