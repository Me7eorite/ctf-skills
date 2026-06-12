<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'

const open = ref(false)

function toggle() {
  open.value = !open.value
}

function close() {
  open.value = false
}

function onClickOutside(event: MouseEvent) {
  if (event.target instanceof Element && !event.target.closest('[data-dropdown-root]')) {
    open.value = false
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('click', onClickOutside)
}

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') window.removeEventListener('click', onClickOutside)
})

defineExpose({ open, close })
</script>

<template>
  <div
    data-dropdown-root
    class="relative inline-block"
  >
    <button
      type="button"
      class="inline-flex h-6 items-center gap-1 rounded-md border border-neutral-200 bg-white px-3 text-body hover:bg-neutral-100"
      @click="toggle"
    >
      <slot name="trigger">
        Menu
      </slot>
    </button>
    <div
      v-if="open"
      role="menu"
      class="absolute right-0 z-20 mt-1 min-w-[200px] rounded-md border border-neutral-200 bg-white p-1 shadow-card"
    >
      <slot :close="close" />
    </div>
  </div>
</template>
