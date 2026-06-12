<script setup lang="ts">
import { useRouter } from 'vue-router'

import Command, { type CommandEntry } from '@/components/ui/Command.vue'
import { useUIStore } from '@/stores/ui'

const ui = useUIStore()
const router = useRouter()

const entries: CommandEntry[] = [
  { id: 'overview', label: 'Overview', group: 'Pages', to: '/' },
  { id: 'new-run', label: 'New Run', group: 'Generate', to: '/generate/new' },
  { id: 'runs', label: 'Runs', group: 'Generate', to: '/generate/runs' },
  { id: 'queue', label: 'Queue', group: 'Operate', to: '/operate/queue' },
  { id: 'workers', label: 'Workers', group: 'Operate', to: '/operate/workers' },
  { id: 'logs', label: 'Logs', group: 'Operate', to: '/operate/logs' },
  { id: 'settings-llm', label: 'Settings · LLM Provider', group: 'Settings', to: '/settings/llm' },
  {
    id: 'settings-profile',
    label: 'Settings · Generation Profile',
    group: 'Settings',
    to: '/settings/profile',
  },
]

function select(entry: CommandEntry) {
  if (entry.to) router.push(entry.to)
}

defineExpose({ entries })
</script>

<template>
  <Command
    :open="ui.commandOpen"
    :entries="entries"
    @update:open="(value: boolean) => (ui.commandOpen = value)"
    @select="select"
  />
</template>
