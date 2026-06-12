<script setup lang="ts">
withDefaults(
  defineProps<{ width?: string; height?: string; rounded?: boolean }>(),
  { width: '100%', height: '12px', rounded: true },
)
</script>

<template>
  <div
    role="status"
    aria-label="loading"
    class="animate-pulse bg-neutral-200"
    :class="rounded ? 'rounded-md' : ''"
    :style="{ width, height }"
  />
</template>
