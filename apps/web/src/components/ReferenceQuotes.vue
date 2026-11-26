<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, type Row } from '../api'
import { useLoad } from '../load'
import { useConsole } from '../store'
const store = useConsole(),
  quotes = ref<Row[]>([])
const { error } = useLoad(async (signal) => {
  quotes.value = (
    await api<Row[]>('reference-prices?limit=50', { signal })
  ).data
})
const cards = computed(() =>
  ['BTC', 'ETH'].map((asset) => {
    const row = quotes.value.find((r) => (r.data as Row)?.asset === asset),
      data = row?.data as Row | undefined
    return {
      asset,
      source: row?.source ?? '—',
      price: data?.price,
      received: row?.received_at,
      quality: row?.quality_flags as string[] | undefined,
    }
  }),
)
</script>
<template>
  <p v-if="error" class="error">{{ error }}</p>
  <div class="book-grid">
    <article v-for="c in cards" :key="c.asset" class="stat">
      <div>
        {{ c.asset }} / USD 外部参考价 <span class="tag">{{ c.source }}</span>
      </div>
      <strong>{{
        c.price == null
          ? '—'
          : Number(c.price).toLocaleString('en-US', {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })
      }}</strong
      ><small
        >最近接收 {{ store.date(c.received) }} {{ store.zone }}
        <span v-if="c.quality?.length"
          >· {{ c.quality.join(', ') }}</span
        ></small
      >
    </article>
  </div>
</template>
