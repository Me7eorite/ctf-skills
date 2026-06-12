<script setup lang="ts">
import { computed, ref, watch } from 'vue'

export interface CommandEntry {
  id: string
  label: string
  hint?: string
  group?: string
  to?: string
}

const props = defineProps<{ open: boolean; entries: CommandEntry[] }>()
const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
  (e: 'select', entry: CommandEntry): void
}>()

const query = ref('')

const filtered = computed(() => {
  const term = query.value.trim().toLowerCase()
  if (!term) return props.entries
  return props.entries.filter((entry) => {
    const haystack = `${entry.label} ${entry.hint ?? ''} ${entry.group ?? ''}`.toLowerCase()
    return haystack.includes(term)
  })
})

watch(
  () => props.open,
  (open) => {
    if (!open) query.value = ''
  },
)

function pick(entry: CommandEntry) {
  emit('select', entry)
  emit('update:open', false)
}

defineExpose({ filtered })
</script>

<template>
  <teleport to="body">
    <transition name="page">
      <div
        v-if="open"
        class="fixed inset-0 z-50 flex items-start justify-center pt-12"
        @click.self="emit('update:open', false)"
      >
        <div class="absolute inset-0 bg-black/30" />
        <div
          role="dialog"
          aria-modal="true"
          class="relative z-10 w-full max-w-lg rounded-lg bg-white p-2 shadow-card"
        >
          <input
            v-model="query"
            placeholder="跳转到任意位置…"
            class="w-full rounded-md border border-neutral-200 px-3 py-2 text-body focus:border-info-400 focus:outline-none"
            autofocus
          >
          <ul
            role="listbox"
            class="mt-2 max-h-72 overflow-y-auto"
          >
            <li
              v-for="entry in filtered"
              :key="entry.id"
              role="option"
              class="flex cursor-pointer items-center justify-between rounded-md px-3 py-2 text-body hover:bg-neutral-100"
              @click="pick(entry)"
            >
              <span>{{ entry.label }}</span>
              <span
                v-if="entry.group"
                class="text-caption text-neutral-400"
              >
                {{ entry.group }}
              </span>
            </li>
            <li
              v-if="filtered.length === 0"
              class="px-3 py-2 text-body text-neutral-500"
            >
              没有匹配的命令
            </li>
          </ul>
        </div>
      </div>
    </transition>
  </teleport>
</template>
