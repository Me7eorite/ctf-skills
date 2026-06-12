<script setup lang="ts">
import { computed } from 'vue'

type Tone = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'accent'

const props = withDefaults(defineProps<{ tone?: Tone }>(), { tone: 'neutral' })

const toneClass = computed(() => {
  switch (props.tone) {
    case 'success':
      return 'bg-success-50 text-success-700 ring-success-200'
    case 'warning':
      return 'bg-warning-50 text-warning-700 ring-warning-200'
    case 'danger':
      return 'bg-danger-50 text-danger-700 ring-danger-200'
    case 'info':
      return 'bg-info-50 text-info-700 ring-info-200'
    case 'accent':
      return 'bg-accent-50 text-accent-700 ring-accent-200'
    default:
      return 'bg-neutral-100 text-neutral-700 ring-neutral-200'
  }
})
</script>

<template>
  <span
    :class="[
      'inline-flex items-center rounded-sm px-2 text-caption font-medium ring-1 ring-inset',
      toneClass,
    ]"
  >
    <slot />
  </span>
</template>
