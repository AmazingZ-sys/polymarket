<script setup lang="ts">
import { ref } from 'vue'
import { NModal } from 'naive-ui'
import { X } from 'lucide-vue-next'
import { api, query, type Row } from '../api'
import { money } from '../fields'
import { useLoad } from '../load'
import Chart from './Chart.vue'
const props = defineProps<{ experiment: Row }>(),
  emit = defineEmits<{ close: [] }>()
const rows = ref<{ id: string; metrics: Row; snapshots: Row[]; config: Row }[]>(
  [],
)
const { busy, error, refresh } = useLoad(async (signal) => {
  const ids = Array.isArray(props.experiment.account_ids)
    ? props.experiment.account_ids.map(String)
    : []
  const fixed = Array.isArray(props.experiment.snapshot)
    ? (props.experiment.snapshot as Row[])
    : []
  rows.value = await Promise.all(
    ids.map(async (id) => {
      const [metrics, equity] = await Promise.all([
        api<Row[]>('analytics' + query({ account_id: id, limit: 1 }), {
          signal,
        }),
        api<Row[]>('equity' + query({ account_id: id, limit: 200 }), {
          signal,
        }),
      ])
      return {
        id,
        metrics: metrics.data[0] || {},
        snapshots: equity.data,
        config: (fixed.find((s) => s.id === id)?.config as Row) || {},
      }
    }),
  )
})
function ratio(v: unknown) {
  return v == null
    ? '—'
    : new Intl.NumberFormat('zh-CN', {
        style: 'percent',
        maximumFractionDigits: 2,
      }).format(Number(v))
}
</script>
<template>
  <NModal
    :show="true"
    @update:show="
      (v) => {
        if (!v) emit('close')
      }
    "
    ><section class="dialog wide compare" role="dialog" aria-label="对照实验">
      <div class="panel-head">
        <div>
          <small class="kicker">COMPARISON</small>
          <h2>{{ experiment.name }}</h2>
        </div>
        <button class="icon" aria-label="关闭" @click="emit('close')">
          <X :size="18" />
        </button>
      </div>
      <dl class="brief">
        <dt>研究假设</dt>
        <dd>{{ experiment.hypothesis }}</dd>
        <dt>验收标准</dt>
        <dd>{{ experiment.criteria }}</dd>
      </dl>
      <p v-if="error" class="error">
        {{ error }} <button class="btn" @click="refresh">重试</button>
      </p>
      <p v-if="busy && !rows.length" class="empty">正在读取实验账户…</p>
      <article v-for="r in rows" :key="r.id" class="panel">
        <div class="panel-head">
          <h2>
            <router-link
              :to="'/paper-accounts/' + r.id"
              @click="emit('close')"
              >{{ r.metrics.name || r.id }}</router-link
            >
          </h2>
          <span class="tag">{{ r.metrics.status }}</span>
        </div>
        <dl class="brief">
          <dt>策略 / 版本</dt>
          <dd>
            {{ r.metrics.strategy_id }} / {{ r.metrics.strategy_version }}
          </dd>
          <dt>撮合模型</dt>
          <dd>{{ r.config.simulation_model || '—' }}</dd>
          <dt>扣费模拟净收益</dt>
          <dd>
            {{ money(r.metrics.net_pnl) }} ·
            {{
              Number(r.metrics.initial_balance) > 0
                ? ratio(
                    Number(r.metrics.net_pnl) /
                      Number(r.metrics.initial_balance),
                  )
                : '—'
            }}
          </dd>
          <dt>最大回撤 / 费用</dt>
          <dd>
            {{ ratio(r.metrics.max_drawdown) }} / {{ money(r.metrics.fees) }}
          </dd>
          <dt>结算市场数 / 胜率</dt>
          <dd>
            {{ r.metrics.settlements ?? '—' }} / {{ ratio(r.metrics.win_rate) }}
          </dd>
        </dl>
        <Chart :rows="r.snapshots" time-key="created_at" />
      </article></section
  ></NModal>
</template>
