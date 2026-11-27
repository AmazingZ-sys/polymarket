<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowUpRight, Inbox, X } from 'lucide-vue-next'
import { NModal } from 'naive-ui'
import type { Row } from '../api'
import { backtestRow, field, keysFor, label } from '../fields'
import { useConsole } from '../store'
const props = defineProps<{
  rows: Row[]
  resource: string
  loading?: boolean
  empty?: string
  openRow?: (row: Row) => void
}>()
const displayed = computed(() =>
  props.resource === 'backtests' ? props.rows.map(backtestRow) : props.rows,
)
const keys = computed(() => keysFor(props.resource, displayed.value))
const store = useConsole(),
  details = ref<Row | null>(null)
let afterClose: (() => void) | undefined
function closeForAction(action: () => void) {
  afterClose = action
  details.value = null
}
function finishClosing() {
  const action = afterClose
  afterClose = undefined
  action?.()
}
function inspect(row: Row) {
  if (props.openRow) props.openRow(row)
  else details.value = row
}
</script>
<template>
  <div class="table-wrap" :aria-busy="loading">
    <table>
      <thead>
        <tr>
          <th v-for="k in keys" :key="k">{{ label(k) }}</th>
          <th>详情</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(r, i) in displayed"
          :key="String(r.id ?? r.market_id ?? i) + '-' + i"
        >
          <td v-for="k in keys" :key="k">
            <span
              :class="{
                tag: k === 'status' || k === 'market_status',
                asset: k === 'asset',
                'primary-cell': k === keys[0],
              }"
              :title="field(k, r[k], store.date)"
              >{{ field(k, r[k], store.date) }}</span
            >
          </td>
          <td>
            <button class="icon" aria-label="查看记录" @click="inspect(rows[i])">
              <ArrowUpRight :size="16" />
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <div v-if="!rows.length" class="empty">
    <Inbox :size="25" /><strong>{{
      loading ? '正在读取…' : empty || '暂无记录'
    }}</strong>
  </div>
  <NModal
    :show="!!details"
    @after-leave="finishClosing"
    @update:show="
      (v) => {
        if (!v) details = null
      }
    "
    ><div class="dialog wide" role="dialog" aria-modal="true" aria-label="记录详情">
      <div class="panel-head">
        <h2>记录详情</h2>
        <button class="icon" aria-label="关闭详情" @click="details = null">
          <X :size="18" />
        </button>
      </div>
      <dl class="details">
        <template v-for="(v, k) in details" :key="k"
          ><dt>{{ label(String(k)) }}</dt>
          <dd>
            <pre v-if="v && typeof v === 'object'">{{
              JSON.stringify(v, null, 2)
            }}</pre>
            <span v-else>{{ field(String(k), v, store.date) }}</span>
          </dd></template
        >
      </dl>
      <slot
        name="record-action"
        :row="details"
        :close="closeForAction"
      /></div
  ></NModal>
</template>
