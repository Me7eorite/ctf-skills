import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUIStore = defineStore('ui', () => {
  const sidebarCollapsed = ref(false)
  const commandOpen = ref(false)

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  function openCommand() {
    commandOpen.value = true
  }

  function closeCommand() {
    commandOpen.value = false
  }

  return {
    sidebarCollapsed,
    commandOpen,
    toggleSidebar,
    openCommand,
    closeCommand,
  }
})
