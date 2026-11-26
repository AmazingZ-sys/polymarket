<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, type Row } from '../api'
import { useConsole } from '../store'
import { useLoad } from '../load'

const store = useConsole()
const newest = ref<Row[]>([]),
  first = ref<Row>(),
  counts = ref<Record<string, number>>({})
const { error } = useLoad(async (signal) => {
  const [latest, oldest, books, prices, issues] = await Promise.all([
    api<Row[]>('market-events?limit=50', { signal }),
    api<Row[]>('market-events?limit=1&direction=asc', { signal }),
    api<Row[]>('orderbooks?limit=1', { signal }),
    api<Row[]>('reference-prices?limit=1', { signal }),
    api<Row[]>('data-quality?limit=1', { signal }),
  ])
  newest.value = latest.data
  first.value = oldest.data[0]
  counts.value = {
    events: latest.meta?.total ?? 0,
    books: books.meta?.total ?? 0,
    prices: prices.meta?.total ?? 0,
    quality: issues.meta?.total ?? 0,
  }
})
const coverage = computed(() => {
  const start = Date.parse(String(first.value?.received_at)),
    end = Date.parse(String(newest.value[0]?.received_at))
  return Number.isFinite(start) && end >= start
    ? ((end - start) / 86400000).toFixed(2) + ' 天'
    : '—'
})
const rate = computed(() => {
  const times = newest.value
    .map((row) => Date.parse(String(row.received_at)))
    .filter(Number.isFinite)
  const span = Math.max(...times) - Math.min(...times)
  return times.length > 1 && span > 0
    ? (((times.length - 1) * 1000) / span).toFixed(2) + ' /s'
    : '—'
})
const delay = computed(() => {
  const valid = newest.value.filter(
    (r) =>
      Array.isArray(r.quality_flags) &&
      !r.quality_flags.includes('SOURCE_TIME_UNKNOWN'),
  )
  const delays = valid
    .map(
      (r) =>
        Date.parse(String(r.received_at)) - Date.parse(String(r.event_time)),
    )
    .filter((n) => Number.isFinite(n) && n >= 0)
    .sort((a, b) => a - b)
  return delays.length
    ? delays[Math.floor((delays.length - 1) * 0.5)] + ' ms'
    : '—'
})
</script>
<template>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <div class="stats">
    <article
      v-for="metric in [
        { n: '已保存事件', v: counts.events, d: '最近 50 条接收速率 ' + rate },
        {
          n: '盘口快照',
          v: counts.books,
          d: '外部参考行情 ' + (counts.prices ?? '—') + ' 条',
        },
        {
          n: '历史观测跨度',
          v: coverage,
          d: '最近 ' + store.date(newest[0]?.received_at, true),
        },
        {
          n: '质量标记事件',
          v: counts.quality,
          d: '近期有效接收延迟 P50 ' + delay,
        },
      ]"
      :key="metric.n"
      class="stat"
    >
      <div>{{ metric.n }}</div>
      <strong>{{ metric.v ?? '—' }}</strong
      ><small>{{ metric.d }}</small>
    </article>
  </div>
</template>
