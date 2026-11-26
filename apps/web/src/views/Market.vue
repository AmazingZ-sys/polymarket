<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft } from 'lucide-vue-next'
import Heading from '../components/Heading.vue'
import Slice from '../components/Slice.vue'
import { api, asRows, query, type Row } from '../api'
import { useLoad } from '../load'
import { useConsole } from '../store'
const id = String(useRoute().params.id),
  m = ref<Row>({}),
  books = ref<Row[]>([]),
  store = useConsole(),
  active = ref('trades'),
  now = ref(Date.now())
let timer: ReturnType<typeof setInterval>
onMounted(() => (timer = setInterval(() => (now.value = Date.now()), 1000)))
onBeforeUnmount(() => clearInterval(timer))
const { error, busy, refresh } = useLoad(async (signal) => {
  const [market, observations] = await Promise.all([
    api<Row[]>('markets' + query({ market_id: id, limit: 1 }), { signal }),
    api<Row[]>('orderbooks' + query({ market_id: id, limit: 64 }), { signal }),
  ])
  m.value = market.data[0] ?? {}
  books.value = observations.data
})
const remaining = computed(() => {
  const t = Date.parse(String(m.value.end_time))
  if (!Number.isFinite(t)) return '—'
  const s = Math.max(0, Math.ceil((t - now.value) / 1000))
  return Math.floor(s / 60) + 'm ' + (s % 60) + 's'
})
function depth(side: string) {
  const row = books.value.find(
    (r) => String((r.data as Row)?.side).toUpperCase() === side,
  )
  const payload = row?.data as Row | undefined
  function levels(key: string) {
    const data = payload?.[key]
    const list = Array.isArray(data)
      ? data
          .filter(Array.isArray)
          .map((pair) => ({ price: pair[0], size: pair[1] }))
      : []
    const converted = list.length
      ? list
      : asRows(data).map((l) => ({
          price: l.price,
          size: l.size ?? l.quantity,
        }))
    return converted
      .sort((a, b) =>
        key === 'asks'
          ? Number(a.price) - Number(b.price)
          : Number(b.price) - Number(a.price),
      )
      .slice(0, 5)
  }
  return { row, asks: levels('asks'), bids: levels('bids') }
}
const depths = computed(() =>
  ['UP', 'DOWN'].map((side) => ({ side, ...depth(side) })),
)
</script>
<template>
  <router-link to="/markets" class="back"
    ><ArrowLeft :size="14" />实时市场</router-link
  ><Heading :title="String(m.title || '市场详情')" en="Market details" />
  <p v-if="error" class="error" role="alert">
    {{ error }} <button class="btn" @click="refresh">重试</button>
  </p>
  <p v-if="!busy && !error && !m.market_id" class="empty">找不到该市场。</p>
  <template v-if="m.market_id"
    ><div class="stats">
      <article
        v-for="s in [
          { n: '资产 / 周期', v: m.asset + ' / ' + m.timeframe + 'm' },
          { n: '距结束', v: remaining },
          { n: '开盘基准价', v: m.reference_price ?? '—' },
          { n: '最终结算结果', v: m.resolved_outcome ?? '待确认' },
        ]"
        :key="s.n"
        class="stat"
      >
        <div>{{ s.n }}</div>
        <strong>{{ s.v }}</strong>
      </article>
    </div>
    <section class="panel">
      <div class="panel-head">
        <h2>结算规则</h2>
        <span class="tag">{{ m.rule_verified ? '已核实' : '未核实' }}</span>
      </div>
      <p class="rule">{{ m.settlement_rule || '等待规则原文' }}</p>
      <dl class="brief">
        <dt>规则版本</dt>
        <dd>{{ m.settlement_rule_version }}</dd>
        <dt>结算来源</dt>
        <dd>{{ m.resolution_source || '未核实' }}</dd>
        <dt>时间范围</dt>
        <dd>
          {{ store.date(m.start_time) }} — {{ store.date(m.end_time) }} ·
          {{ store.zone }}
        </dd>
      </dl>
    </section>
    <div class="book-grid">
      <section v-for="book in depths" :key="book.side" class="panel">
        <div class="panel-head">
          <div>
            <small class="kicker">ORDER BOOK</small>
            <h2>{{ book.side }} 盘口</h2>
          </div>
          <small class="muted">{{
            store.date(book.row?.received_at, true)
          }}</small>
        </div>
        <p v-if="!book.row" class="empty small">等待订单簿快照</p>
        <div v-else class="depth">
          <div v-for="key in ['bids', 'asks'] as const" :key="key">
            <small>{{ key === 'bids' ? '买价 / 深度' : '卖价 / 深度' }}</small>
            <div v-for="(l, i) in book[key]" :key="i" class="level">
              <i
                :class="key"
                :style="{
                  width:
                    (Number(l.size) /
                      Math.max(1, ...book[key].map((v) => Number(v.size)))) *
                      100 +
                    '%',
                }"
              /><span>{{ l.price }}</span
              ><span>{{ l.size }}</span>
            </div>
          </div>
        </div>
      </section>
    </div>
    <div class="tabs">
      <button
        v-for="(name, k) in {
          trades: '市场成交',
          'market-events': '历史时间线',
          opportunities: '关联信号',
        }"
        :key="k"
        :class="{ chosen: active === k }"
        @click="active = String(k)"
      >
        {{ name }}
      </button>
    </div>
    <Slice :key="active" :resource="active" :filters="{ market_id: id }"
  /></template>
</template>
