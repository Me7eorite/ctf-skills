<script setup lang="ts">
import { computed } from 'vue'

import Card from '@/components/ui/Card.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { useApiQuery } from '@/composables/useApi'

interface RunRow {
  name: string
  state: 'pending' | 'running' | 'done' | 'failed'
  challenge_count: number
  categories: string[]
}

const columns: Array<{ id: RunRow['state']; label: string; tone: string }> = [
  { id: 'pending', label: 'Pending', tone: 'bg-neutral-100' },
  { id: 'running', label: 'Running', tone: 'bg-info-50' },
  { id: 'done', label: 'Done', tone: 'bg-success-50' },
  { id: 'failed', label: 'Failed', tone: 'bg-danger-50' },
]

const query = useApiQuery<{ items: RunRow[] }>('queue', '/api/runs?limit=200')

const groups = computed(() => {
  const items = query.data.value?.items ?? []
  return columns.map((column) => ({
    ...column,
    rows: items.filter((row) => row.state === column.id),
  }))
})
</script>

<template>
  <div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
    <Card
      v-for="column in groups"
      :key="column.id"
      :title="column.label"
    >
      <Skeleton
        v-if="query.isLoading.value"
        height="120px"
      />
      <div
        v-else-if="column.rows.length === 0"
        class="text-body text-neutral-500"
      >
        空
      </div>
      <ul
        v-else
        class="space-y-2"
      >
        <li
          v-for="row in column.rows"
          :key="row.name"
          class="rounded-md border border-neutral-200 bg-white p-2 text-body"
          :class="column.tone"
        >
          <router-link
            :to="`/generate/runs/${encodeURIComponent(row.name)}`"
            class="block font-mono text-caption text-info-700 hover:underline"
          >
            {{ row.name }}
          </router-link>
          <p class="mt-1 text-caption text-neutral-600">
            {{ row.challenge_count }} 题 · {{ row.categories.join(', ') || '—' }}
          </p>
        </li>
      </ul>
    </Card>
  </div>
</template>
