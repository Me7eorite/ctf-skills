<script setup lang="ts">
import { RouterView } from 'vue-router'

import AppShell from './components/AppShell.vue'
import CommandPalette from './components/CommandPalette.vue'
import ToastStack from './components/ToastStack.vue'
import { useCommandPaletteHotkey } from './composables/useCommandPaletteHotkey'

useCommandPaletteHotkey()
</script>

<template>
  <AppShell>
    <RouterView v-slot="{ Component }">
      <transition
        name="page"
        mode="out-in"
      >
        <component :is="Component" />
      </transition>
    </RouterView>
  </AppShell>
  <CommandPalette />
  <ToastStack />
</template>

<style>
/* page: 100 ms opacity fade-in for route changes (PAGE_TRANSITION). */
.page-enter-active,
.page-leave-active {
  transition: opacity 100ms ease;
}
.page-enter-from,
.page-leave-to {
  opacity: 0;
}

/* sheet: 200 ms slide-in from the right for Sheet/side panels (SHEET_TRANSITION). */
.sheet-enter-active,
.sheet-leave-active {
  transition:
    opacity 200ms ease,
    transform 200ms ease;
}
.sheet-enter-from,
.sheet-leave-to {
  opacity: 0;
  transform: translateX(16px);
}
</style>
