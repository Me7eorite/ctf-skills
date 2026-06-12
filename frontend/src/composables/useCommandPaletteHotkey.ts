import { onBeforeUnmount, onMounted } from 'vue'

import { useUIStore } from '@/stores/ui'

export function useCommandPaletteHotkey() {
  const ui = useUIStore()

  function handler(event: KeyboardEvent) {
    const key = event.key.toLowerCase()
    if (key !== 'k') return
    if (!(event.metaKey || event.ctrlKey)) return
    event.preventDefault()
    ui.commandOpen = !ui.commandOpen
  }

  onMounted(() => {
    if (typeof window === 'undefined') return
    window.addEventListener('keydown', handler)
  })
  onBeforeUnmount(() => {
    if (typeof window === 'undefined') return
    window.removeEventListener('keydown', handler)
  })
}
