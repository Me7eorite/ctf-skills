<script setup lang="ts">
import { computed } from 'vue'

interface Segment {
  start: number
  end: number
  label?: string
  tone?: 'info' | 'success' | 'warning' | 'danger'
}

const props = defineProps<{ segments: Segment[]; rangeStart: number; rangeEnd: number; label?: string }>()

const span = computed(() => Math.max(1, props.rangeEnd - props.rangeStart))

function toneClass(tone?: Segment['tone']) {
  switch (tone) {
    case 'success':
      return 'bg-success-500'
    case 'warning':
      return 'bg-warning-500'
    case 'danger':
      return 'bg-danger-500'
    default:
      return 'bg-info-500'
  }
}

function leftPercent(start: number) {
  return ((start - props.rangeStart) / span.value) * 100
}

function widthPercent(start: number, end: number) {
  return Math.max(0.5, ((end - start) / span.value) * 100)
}
</script>

<template>
  <div class="flex items-center gap-3">
    <div
      v-if="label"
      class="w-32 truncate text-caption text-neutral-600"
    >
      {{ label }}
    </div>
    <div class="relative h-3 flex-1 rounded-full bg-neutral-100">
      <div
        v-for="(segment, index) in segments"
        :key="index"
        :class="['absolute top-0 h-full rounded-full', toneClass(segment.tone)]"
        :style="{
          left: `${leftPercent(segment.start)}%`,
          width: `${widthPercent(segment.start, segment.end)}%`,
        }"
        :title="segment.label"
      />
    </div>
  </div>
</template>
