import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useConsole } from './store'
import { errorText } from './api'
export function useLoad(load: (signal: AbortSignal) => Promise<void>) {
  const store = useConsole(),
    busy = ref(true),
    error = ref('')
  let active: AbortController | undefined
  async function refresh() {
    active?.abort()
    const current = new AbortController()
    active = current
    busy.value = true
    error.value = ''
    try {
      await load(current.signal)
    } catch (e) {
      if (!current.signal.aborted) error.value = errorText(e)
    } finally {
      if (!current.signal.aborted) busy.value = false
    }
  }
  onMounted(refresh)
  onBeforeUnmount(() => active?.abort())
  watch(
    () => store.revision,
    () => {
      if (!busy.value) void refresh()
    },
  )
  return { refresh, busy, error }
}
