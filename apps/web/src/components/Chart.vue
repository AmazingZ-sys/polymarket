<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { init, use, type ECharts } from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { ChartNoAxesCombined } from 'lucide-vue-next'
import type { Row } from '../api'
import { useConsole } from '../store'
use([LineChart, BarChart, GridComponent, TooltipComponent, CanvasRenderer])
const props = withDefaults(
  defineProps<{
    rows: Row[]
    valueKey?: string
    timeKey?: string
    type?: 'line' | 'bar'
    unit?: string
    empty?: string
  }>(),
  {
    valueKey: 'equity',
    timeKey: 'time',
    type: 'line',
    unit: 'USD',
    empty: '暂无权益快照',
  },
)
const store = useConsole(),
  host = ref<HTMLElement>()
const values = computed(() =>
  props.rows
    .filter(
      (r) =>
        typeof r[props.timeKey] === 'string' &&
        Number.isFinite(Date.parse(String(r[props.timeKey]))) &&
        r[props.valueKey] != null &&
        Number.isFinite(Number(r[props.valueKey])),
    )
    .map((r) => [
      Date.parse(String(r[props.timeKey])),
      Number(r[props.valueKey]),
    ])
    .sort((a, b) => a[0] - b[0]),
)
let chart: ECharts | undefined, observer: ResizeObserver | undefined
function render() {
  if (!host.value) return
  chart ||= init(host.value)
  const style = getComputedStyle(document.documentElement)
  const color = style.getPropertyValue('--accent').trim(),
    border = style.getPropertyValue('--line').trim(),
    muted = style.getPropertyValue('--muted').trim()
  chart.setOption(
    {
      animation: false,
      grid: { top: 16, right: 16, bottom: 30, left: 58 },
      tooltip: { trigger: 'axis', renderMode: 'richText', confine: true },
      xAxis: {
        type: 'time',
        axisLine: { lineStyle: { color: border } },
        axisLabel: {
          color: muted,
          formatter: (t: number) =>
            new Date(t).toLocaleTimeString('zh-CN', {
              hour: '2-digit',
              minute: '2-digit',
              timeZone: store.zone,
            }),
        },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'value',
        scale: true,
        splitNumber: 4,
        axisLabel: { color: muted },
        splitLine: { lineStyle: { color: border, type: 'dashed' } },
      },
      series: [
        {
          type: props.type,
          name: props.unit,
          data: values.value,
          showSymbol: values.value.length < 3,
          symbolSize: 5,
          itemStyle: { color },
          lineStyle: { color, width: 2 },
          areaStyle:
            props.type === 'line' ? { color, opacity: 0.08 } : undefined,
          barMaxWidth: 30,
        },
      ],
    },
    true,
  )
}
watch(
  () => [values.value, store.theme, store.zone, props.type],
  async () => {
    await nextTick()
    render()
  },
)
onMounted(() => {
  render()
  observer = new ResizeObserver(() => chart?.resize())
  if (host.value) observer.observe(host.value)
})
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>
<template>
  <div class="chart-block">
    <div
      ref="host"
      class="chart"
      :style="{ visibility: values.length ? 'visible' : 'hidden' }"
      role="img"
      :aria-label="unit + ' 时间序列，' + values.length + '个观察值'"
    />
    <div v-if="!values.length" class="chart-empty">
      <ChartNoAxesCombined :size="28" /><span>{{ empty }}</span>
    </div>
  </div>
</template>
