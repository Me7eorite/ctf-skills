<script setup lang="ts">
import { computed } from 'vue'

type Tone = 'success' | 'warning' | 'danger' | 'info'

const props = withDefaults(
  defineProps<{ tone?: Tone; title?: string; description?: string }>(),
  { tone: 'info' },
)

const toneClass = computed(() => {
  switch (props.tone) {
    case 'success':
      return 'border-success-200 bg-success-50 text-success-900'
    case 'warning':
      return 'border-warning-200 bg-warning-50 text-warning-900'
    case 'danger':
      return 'border-danger-200 bg-danger-50 text-danger-900'
    default:
      return 'border-info-200 bg-info-50 text-info-900'
  }
})
</script>

<template>
  <div
    role="status"
    aria-live="polite"
    :class="[
      'flex w-full items-start gap-3 rounded-md border p-3 shadow-card',
      toneClass,
    ]"
  >
    <div class="flex-1">
      <p
        v-if="title"
        class="text-body font-semibold"
      >
        {{ title }}
      </p>
      <p
        v-if="description"
        class="text-caption opacity-90"
      >
        {{ description }}
      </p>
      <slot />
    </div>
  </div>
</template>
