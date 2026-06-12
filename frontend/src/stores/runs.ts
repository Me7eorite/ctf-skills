import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface RunRow {
  name: string
  state: 'pending' | 'running' | 'done' | 'failed'
  started_at: number
  challenge_count: number
  challenge_ids: string[]
  pass_rate: number | null
  categories: string[]
}

export const useRunsStore = defineStore('runs', () => {
  const items = ref<RunRow[]>([])
  const total = ref(0)
  const loaded = ref(false)

  function setPage(payload: { items: RunRow[]; total: number }) {
    items.value = payload.items
    total.value = payload.total
    loaded.value = true
  }

  return { items, total, loaded, setPage }
})
