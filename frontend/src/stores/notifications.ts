import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface ToastEntry {
  id: number
  tone: 'success' | 'warning' | 'danger' | 'info'
  title: string
  description?: string
  createdAt: number
}

const MAX_HISTORY = 50

export const useNotificationsStore = defineStore('notifications', () => {
  const items = ref<ToastEntry[]>([])
  let counter = 0

  function push(entry: Omit<ToastEntry, 'id' | 'createdAt'>): ToastEntry {
    counter += 1
    const toast: ToastEntry = {
      id: counter,
      createdAt: Date.now(),
      ...entry,
    }
    items.value = [toast, ...items.value].slice(0, MAX_HISTORY)
    return toast
  }

  function dismiss(id: number) {
    items.value = items.value.filter((entry) => entry.id !== id)
  }

  return { items, push, dismiss }
})
