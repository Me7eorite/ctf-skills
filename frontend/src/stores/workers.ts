import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface WorkerSnapshot {
  running: boolean
  kind?: string
  started_at?: string
  message?: string
}

export const useWorkersStore = defineStore('workers', () => {
  const process = ref<WorkerSnapshot>({ running: false })

  function setProcess(snapshot: WorkerSnapshot) {
    process.value = snapshot
  }

  return { process, setProcess }
})
