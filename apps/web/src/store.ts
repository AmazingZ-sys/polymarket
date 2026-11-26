import { defineStore } from 'pinia'
import { api, ApiError, errorText, eventsUrl } from './api'

export interface Identity {
  username: string
  role: string
}
let stream: EventSource | undefined
export const zones = Array.from(
  new Set([
    'UTC',
    Intl.DateTimeFormat().resolvedOptions().timeZone,
    'Asia/Shanghai',
    'America/New_York',
    'Europe/London',
  ]),
)
export const useConsole = defineStore('console', {
  state: () => ({
    identity: null as Identity | null,
    ready: false,
    serviceError: '',
    revision: 0,
    connection: 'OFFLINE',
    theme: localStorage.getItem('pq-theme') === 'light' ? 'light' : 'dark',
    zone: zones.find((z) => z === localStorage.getItem('pq-zone')) || 'UTC',
  }),
  getters: { canWrite: (s) => s.identity?.role?.toLowerCase() === 'admin' },
  actions: {
    async initialize() {
      this.ready = false
      this.serviceError = ''
      try {
        this.setIdentity((await api<Identity>('auth/me')).data)
      } catch (e) {
        if (!(e instanceof ApiError) || e.status !== 401)
          this.serviceError = errorText(e)
      } finally {
        this.ready = true
      }
    },
    setIdentity(identity: Identity | null) {
      stream?.close()
      stream = undefined
      this.identity = identity
      this.connection = identity ? 'CONNECTING' : 'OFFLINE'
      if (!identity) return
      stream = new EventSource(eventsUrl, { withCredentials: true })
      stream.onopen = () => {
        this.connection = 'ONLINE'
      }
      stream.onerror = () => {
        this.connection = 'RECONNECTING'
      }
      stream.addEventListener('refresh', () => {
        this.revision++
      })
    },
    async login(username: string, password: string) {
      this.setIdentity(
        (await api<Identity>('auth/login', { body: { username, password } }))
          .data,
      )
      this.serviceError = ''
    },
    async logout() {
      await api('auth/logout', { body: {} })
      this.setIdentity(null)
    },
    setTheme(value: string) {
      this.theme = value
      localStorage.setItem('pq-theme', value)
    },
    setZone(value: string) {
      this.zone = value
      localStorage.setItem('pq-zone', value)
    },
    date(value: unknown, compact = false) {
      if (typeof value !== 'string' || !Number.isFinite(Date.parse(value)))
        return '—'
      return new Intl.DateTimeFormat('zh-CN', {
        timeZone: this.zone,
        month: '2-digit',
        day: '2-digit',
        ...(compact ? {} : { year: 'numeric' }),
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }).format(new Date(value))
    },
  },
})
