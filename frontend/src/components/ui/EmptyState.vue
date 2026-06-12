<script setup lang="ts">
defineProps<{
  title: string
  description?: string
  illustration?: string
  ctaLabel?: string
  ctaTo?: string
}>()
const emit = defineEmits<{ (e: 'cta'): void }>()
</script>

<template>
  <div class="flex flex-col items-center justify-center gap-4 py-8 text-center">
    <img
      v-if="illustration"
      :src="illustration"
      alt=""
      class="h-12 w-12 opacity-80"
    >
    <div>
      <h3 class="text-h2 font-semibold text-neutral-900">
        {{ title }}
      </h3>
      <p
        v-if="description"
        class="mt-1 text-body text-neutral-500"
      >
        {{ description }}
      </p>
    </div>
    <component
      :is="ctaTo ? 'router-link' : 'button'"
      v-if="ctaLabel"
      :to="ctaTo"
      type="button"
      class="inline-flex h-6 items-center rounded-md bg-info-600 px-3 text-body font-medium text-white hover:bg-info-700"
      @click="!ctaTo && emit('cta')"
    >
      {{ ctaLabel }}
    </component>
  </div>
</template>
