<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ values: number[]; width?: number; height?: number; tone?: 'info' | 'success' | 'danger' }>(),
  { width: 120, height: 32, tone: 'info' },
)

const path = computed(() => {
  if (!props.values.length) return ''
  const max = Math.max(...props.values, 1)
  const min = Math.min(...props.values, 0)
  const span = Math.max(1, max - min)
  const stepX = props.width / Math.max(1, props.values.length - 1)
  return props.values
    .map((value, index) => {
      const x = index * stepX
      const y = props.height - ((value - min) / span) * props.height
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
})

const stroke = computed(() => {
  switch (props.tone) {
    case 'success':
      return '#10b981'
    case 'danger':
      return '#ef4444'
    default:
      return '#3b82f6'
  }
})
</script>

<template>
  <svg
    :width="width"
    :height="height"
    role="img"
    aria-label="trend"
  >
    <path
      :d="path"
      :stroke="stroke"
      fill="none"
      stroke-width="2"
      stroke-linejoin="round"
    />
  </svg>
</template>
