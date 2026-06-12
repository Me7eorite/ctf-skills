<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'

const props = defineProps<{ open: boolean; title?: string }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>()

function close() {
  emit('update:open', false)
}

function onEscape(event: KeyboardEvent) {
  if (event.key === 'Escape') close()
}

watch(
  () => props.open,
  (open) => {
    if (typeof window === 'undefined') return
    if (open) window.addEventListener('keydown', onEscape)
    else window.removeEventListener('keydown', onEscape)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') window.removeEventListener('keydown', onEscape)
})
</script>

<template>
  <teleport to="body">
    <transition name="page">
      <div
        v-if="open"
        class="fixed inset-0 z-50 flex items-center justify-center"
        @click.self="close"
      >
        <div class="absolute inset-0 bg-black/40" />
        <div
          role="dialog"
          aria-modal="true"
          class="relative z-10 w-full max-w-lg rounded-lg bg-white p-4 shadow-card"
        >
          <header
            v-if="title"
            class="mb-3"
          >
            <h2 class="text-h2 font-semibold">
              {{ title }}
            </h2>
          </header>
          <slot />
        </div>
      </div>
    </transition>
  </teleport>
</template>
