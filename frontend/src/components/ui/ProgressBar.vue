<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ value: number; max?: number; tone?: 'info' | 'success' | 'warning' | 'danger' }>(),
  { max: 100, tone: 'info' },
)

const percent = computed(() => Math.min(100, Math.max(0, (props.value / props.max) * 100)))

const toneClass = computed(() => {
  switch (props.tone) {
    case 'success':
      return 'bg-success-500'
    case 'warning':
      return 'bg-warning-500'
    case 'danger':
      return 'bg-danger-500'
    default:
      return 'bg-info-500'
  }
})
</script>

<template>
  <div
    role="progressbar"
    :aria-valuemin="0"
    :aria-valuemax="max"
    :aria-valuenow="value"
    class="h-2 w-full overflow-hidden rounded-full bg-neutral-200"
  >
    <div
      :class="['h-full transition-all', toneClass]"
      :style="{ width: `${percent}%` }"
    />
  </div>
</template>
