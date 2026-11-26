<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
const host = ref<HTMLCanvasElement>()
let observer: ResizeObserver | undefined
function draw() {
  const canvas = host.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const w = canvas.clientWidth,
    h = canvas.clientHeight,
    dpr = Math.min(devicePixelRatio || 1, 2)
  canvas.width = w * dpr
  canvas.height = h * dpr
  ctx.scale(dpr, dpr)
  const scale = Math.min(w * 0.32, h * 0.355)
  function project(theta: number, phi: number) {
    const tube = 0.35 + 0.08 * Math.cos(3 * theta)
    const radius = 1 + tube * Math.cos(phi)
    const x = radius * Math.cos(theta),
      y = radius * Math.sin(theta),
      z = tube * Math.sin(phi) + 0.29 * Math.sin(2 * theta)
    const a = y * 0.48 - z * 0.88,
      depth = y * 0.88 + z * 0.48
    return {
      x: w * 0.5 + scale * (x * 0.85 - a * 0.52),
      y: h * 0.51 + scale * (x * 0.52 + a * 0.85),
      depth,
    }
  }
  for (let ring = 0; ring < 124; ring++) {
    const angle = (ring * Math.PI) / 62
    for (let j = 0; j < 76; j++) {
      const u = (j * Math.PI) / 38
      const a = project(angle, u),
        b = project(angle, u + Math.PI / 38)
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.lineTo(b.x, b.y)
      const light = Math.min(1, Math.max(0.08, (a.depth + 1.4) / 2.5))
      ctx.strokeStyle =
        'rgba(' +
        Math.round(52 + light * 97) +
        ',' +
        Math.round(108 + light * 116) +
        ',' +
        Math.round(76 + light * 94) +
        ',' +
        (0.16 + 0.68 * light) +
        ')'
      ctx.lineWidth = 0.72
      ctx.stroke()
    }
  }
}
onMounted(() => {
  observer = new ResizeObserver(draw)
  if (host.value) observer.observe(host.value)
  draw()
})
onBeforeUnmount(() => observer?.disconnect())
</script>
<template><canvas ref="host" class="wire-art" aria-hidden="true" /></template>
