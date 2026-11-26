<script setup lang="ts">
defineProps<{ title: string; en: string; detail?: string }>()
</script>
<template>
  <div class="heading">
    <div>
      <div class="eyebrow"><span />{{ en.toUpperCase() }}</div>
      <h1>{{ title }}</h1>
      <p v-if="detail" class="muted">{{ detail }}</p>
    </div>
    <div class="actions"><slot /></div>
  </div>
</template>
