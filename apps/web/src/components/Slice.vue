<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  ArrowLeft,
  ArrowRight,
  Download,
  RefreshCw,
  Search,
} from 'lucide-vue-next'
import { api, query, rowsToFile, type Row, errorText } from '../api'
import { useLoad } from '../load'
import Table from './Table.vue'
const props = withDefaults(
  defineProps<{
    resource: string
    filters?: Record<string, string>
    title?: string
    openRow?: (r: Row) => void
    searchable?: boolean
  }>(),
  { filters: () => ({}), searchable: true },
)
const rows = ref<Row[]>([]),
  total = ref(0),
  offset = ref(0),
  term = ref(''),
  q = ref(''),
  limit = ref(20),
  format = ref('json'),
  exporting = ref(false),
  exportError = ref('')
const base = computed(() => ({ ...props.filters, q: q.value }))
const { busy, error, refresh } = useLoad(async (signal) => {
  const result = await api<Row[]>(
    props.resource +
      query({ ...base.value, limit: limit.value, offset: offset.value }),
    { signal },
  )
  rows.value = result.data
  total.value = result.meta?.total ?? result.data.length
})
watch(
  [() => props.resource, () => JSON.stringify(props.filters), q, limit],
  () => {
    rows.value = []
    if (offset.value === 0) void refresh()
    else offset.value = 0
  },
)
watch(offset, refresh)
watch([busy, rows], (_, __, onCleanup) => {
  if (props.resource === 'backtests' && !busy.value && !error.value &&
      rows.value.some((row) => row.status === 'QUEUED' || row.status === 'RUNNING')) {
    const timer = window.setTimeout(refresh, 5000)
    onCleanup(() => window.clearTimeout(timer))
  }
})
function showNewest() {
  term.value = ''
  if (!q.value && !offset.value) return refresh()
  q.value = ''
  offset.value = 0
}
defineExpose({ refresh, showNewest })
async function download() {
  if (exporting.value) return
  exporting.value = true
  exportError.value = ''
  try {
    const output: Row[] = []
    const resource = props.resource,
      filters = { ...base.value },
      exportFormat = format.value
    let count = 0
    do {
      const result = await api<Row[]>(
        resource +
          query({
            ...filters,
            limit: 200,
            offset: output.length,
            direction: 'asc',
          }),
      )
      output.push(...result.data)
      count = result.meta?.total ?? output.length
      if (!result.data.length) break
    } while (output.length < count)
    rowsToFile(output, exportFormat, resource)
  } catch (e) {
    exportError.value = errorText(e)
  } finally {
    exporting.value = false
  }
}
</script>
<template>
  <section class="panel">
    <div v-if="title" class="panel-head">
      <h2>{{ title }}</h2>
    </div>
    <div class="table-toolbar">
      <form v-if="searchable" class="search" @submit.prevent="q = term.trim()">
        <Search :size="14" /><input
          v-model="term"
          aria-label="搜索记录"
          maxlength="200"
          placeholder="搜索记录"
        /><button class="icon" type="submit" aria-label="执行搜索">
          <ArrowRight :size="14" />
        </button>
      </form>
      <slot name="filters" />
      <div class="table-tools">
        <button class="icon" aria-label="刷新记录" @click="refresh">
          <RefreshCw :size="14" :class="{ spin: busy }" /></button
        ><select v-model="format" aria-label="导出格式">
          <option value="json">JSON</option>
          <option value="csv">CSV</option>
          <option value="md">Markdown</option></select
        ><button
          class="btn compact"
          :disabled="exporting || !total"
          @click="download"
        >
          <Download :size="13" />{{ exporting ? '导出中' : '导出' }}
        </button>
      </div>
    </div>
    <div v-if="error || exportError" class="error inset" role="alert">
      {{ error || exportError }}
    </div>
    <Table
      v-if="!error"
      :rows="rows"
      :resource="resource"
      :loading="busy"
      :empty="resource === 'backtests'
        ? (q ? '没有匹配的回测任务。' : '尚未提交回测任务。新建回测可查看已有行情覆盖的 UTC 区间。')
        : undefined"
      :open-row="openRow"
      ><template #record-action="slot"
        ><slot name="record-action" v-bind="slot" /></template
    ></Table>
    <div class="pagination">
      <span
        >共 {{ total }} {{ resource === 'backtests' ? '个回测任务' : '条' }}<span v-if="total">
          · {{ offset + 1 }}–{{ Math.min(offset + limit, total) }}</span
        ></span
      >
      <div>
        <select v-model.number="limit" aria-label="每页数量">
          <option v-for="n in [20, 50, 100]" :key="n" :value="n">
            {{ n }} 条 / 页
          </option></select
        ><button
          class="icon"
          :disabled="busy || offset === 0"
          aria-label="上一页"
          @click="offset -= limit"
        >
          <ArrowLeft :size="14" /></button
        ><button
          class="icon"
          :disabled="busy || offset + limit >= total"
          aria-label="下一页"
          @click="offset += limit"
        >
          <ArrowRight :size="14" />
        </button>
      </div>
    </div>
  </section>
</template>
