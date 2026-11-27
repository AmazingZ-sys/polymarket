import { computed, ref, watch, type Ref } from 'vue'
import { api, errorText, query } from './api'

interface ReplayWindow {
  start_time: string
  end_time: string
  event_count: number
}

export function utcPeriod(start: string, end: string) {
  const from = Date.parse(start + 'Z')
  const to = Date.parse(end + 'Z')
  if (!Number.isFinite(from) || !Number.isFinite(to) || from >= to) return null
  return { start_time: new Date(from).toISOString(), end_time: new Date(to).toISOString() }
}

export function useBacktestWindow(
  enabled: boolean,
  assets: Ref<string[]>,
  periods: Ref<number[]>,
  dates: { start: string; end: string },
) {
  const manual = ref(false)
  const generation = ref(0)
  const coverage = ref<ReplayWindow | null>(null)
  const message = ref('')
  const request = computed(() => {
    if (!enabled || !assets.value.length || !periods.value.length) return ''
    const period = manual.value ? utcPeriod(dates.start, dates.end) : undefined
    if (period === null) return ''
    return 'backtests/window' + query({
      assets: assets.value.join(','),
      timeframes: periods.value.join(','),
      ...period,
    })
  })
  watch([request, generation], async ([path], _, onCleanup) => {
    coverage.value = null
    if (!enabled) return
    if (!manual.value) {
      dates.start = ''
      dates.end = ''
    }
    if (!path) {
      message.value = !assets.value.length || !periods.value.length
        ? '请先选择资产和市场周期。'
        : '请填写有效 UTC 区间，截止时间须晚于起始时间。'
      return
    }
    const controller = new AbortController()
    onCleanup(() => controller.abort())
    message.value = '正在核对所选市场的历史事件…'
    try {
      const { data } = await api<ReplayWindow | null>(path, { signal: controller.signal })
      if (controller.signal.aborted) return
      if (!data?.event_count) {
        message.value = manual.value
          ? '所选 UTC 区间有 0 条市场事件，请调整时间范围。'
          : '所选资产和周期暂无历史市场事件。采集或导入后可重新查询。'
        return
      }
      if (!manual.value) {
        dates.start = data.start_time.slice(0, -1)
        dates.end = data.end_time.slice(0, -1)
      }
      coverage.value = data
      message.value = `${manual.value ? '所选' : '最近有数据的'} UTC 区间内有 ${data.event_count} 条所选市场事件。`
    } catch (error) {
      if (!controller.signal.aborted) message.value = errorText(error)
    }
  }, { immediate: true })

  function useLatest() {
    manual.value = false
    generation.value++
  }
  return { manual, coverage, message, useLatest }
}
