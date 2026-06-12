<script setup lang="ts">
defineProps<{
  title?: string
  description?: string
}>()
</script>

<template>
  <section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-card">
    <header
      v-if="title || $slots.header"
      class="mb-3"
    >
      <slot name="header">
        <h2 class="text-h2 font-semibold text-neutral-900">
          {{ title }}
        </h2>
        <p
          v-if="description"
          class="text-caption text-neutral-500"
        >
          {{ description }}
        </p>
      </slot>
    </header>
    <slot />
    <footer
      v-if="$slots.footer"
      class="mt-3 border-t border-neutral-200 pt-3"
    >
      <slot name="footer" />
    </footer>
  </section>
</template>
