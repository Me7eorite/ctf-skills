<script setup lang="ts">
import { computed } from 'vue'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'lg'

const props = withDefaults(
  defineProps<{
    variant?: Variant
    size?: Size
    disabled?: boolean
    type?: 'button' | 'submit' | 'reset'
  }>(),
  { variant: 'primary', size: 'md', disabled: false, type: 'button' },
)

const variantClass = computed(() => {
  switch (props.variant) {
    case 'secondary':
      return 'bg-neutral-100 text-neutral-900 hover:bg-neutral-200'
    case 'ghost':
      return 'bg-transparent text-neutral-700 hover:bg-neutral-100'
    case 'danger':
      return 'bg-danger-500 text-white hover:bg-danger-600'
    default:
      return 'bg-info-600 text-white hover:bg-info-700'
  }
})

const sizeClass = computed(() => {
  switch (props.size) {
    case 'sm':
      return 'h-6 px-2 text-caption'
    case 'lg':
      return 'h-8 px-4 text-h2'
    default:
      return 'h-6 px-3 text-body'
  }
})
</script>

<template>
  <button
    :type="type"
    :disabled="disabled"
    :class="[
      'inline-flex items-center justify-center gap-1 rounded-md font-medium transition-colors',
      'disabled:cursor-not-allowed disabled:opacity-50',
      variantClass,
      sizeClass,
    ]"
  >
    <slot />
  </button>
</template>
