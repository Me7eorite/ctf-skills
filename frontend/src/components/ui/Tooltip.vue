<script setup lang="ts">
import { ref } from 'vue'

defineProps<{ label: string }>()
const visible = ref(false)
</script>

<template>
  <span
    class="relative inline-flex"
    @mouseenter="visible = true"
    @mouseleave="visible = false"
    @focusin="visible = true"
    @focusout="visible = false"
  >
    <slot />
    <span
      v-if="visible"
      role="tooltip"
      class="pointer-events-none absolute -top-8 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-sm bg-neutral-900 px-2 text-caption text-white"
    >
      {{ label }}
    </span>
  </span>
</template>
