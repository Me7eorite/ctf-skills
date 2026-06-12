<script setup lang="ts">
defineProps<{ open: boolean; title?: string; side?: 'right' | 'left' }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>()
</script>

<template>
  <teleport to="body">
    <transition name="sheet">
      <div
        v-if="open"
        class="fixed inset-0 z-40"
        @click.self="emit('update:open', false)"
      >
        <div class="absolute inset-0 bg-black/30" />
        <aside
          :class="[
            'absolute top-0 z-10 h-full w-[420px] bg-white p-4 shadow-card',
            side === 'left' ? 'left-0' : 'right-0',
          ]"
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
        </aside>
      </div>
    </transition>
  </teleport>
</template>
