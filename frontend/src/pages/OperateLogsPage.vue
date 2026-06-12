<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import Card from '@/components/ui/Card.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { useApiQuery } from '@/composables/useApi'

interface DashboardState {
  logs?: Array<{ name: string; size: number; updated: string }>
}

const list = useApiQuery<DashboardState>('state', '/api/state')
const selected = ref<string | null>(null)
const search = ref('')

const logQuery = useApiQuery<{ name: string; content: string }>(
  computed(() => ['log', selected.value ?? '']) as never,
  computed(() => (selected.value ? `/api/logs/${encodeURIComponent(selected.value)}` : '/api/state'))
    .value,
)

watch(selected, () => {
  if (selected.value) logQuery.refetch()
})

const filteredLines = computed(() => {
  const content = logQuery.data.value?.content ?? ''
  const term = search.value.trim().toLowerCase()
  const lines = content.split('\n')
  if (!term) return lines.slice(-500)
  return lines.filter((line) => line.toLowerCase().includes(term)).slice(-500)
})
</script>

<template>
  <div class="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
    <Card title="Log files">
      <Skeleton
        v-if="list.isLoading.value"
        height="120px"
      />
      <EmptyState
        v-else-if="!list.data.value?.logs?.length"
        title="还没有日志"
        description="启动一个 worker 之后会出现日志"
      />
      <ul
        v-else
        class="divide-y divide-neutral-100 text-body"
      >
        <li
          v-for="log in list.data.value.logs"
          :key="log.name"
          class="py-1"
        >
          <button
            type="button"
            class="block w-full rounded-md px-2 py-1 text-left hover:bg-neutral-100"
            :class="{ 'bg-neutral-100 font-medium': selected === log.name }"
            @click="selected = log.name"
          >
            <span class="font-mono text-caption">{{ log.name }}</span>
            <span class="block text-caption text-neutral-500">
              {{ log.size }} B · {{ log.updated }}
            </span>
          </button>
        </li>
      </ul>
    </Card>

    <Card>
      <template #header>
        <div class="flex items-center justify-between">
          <h2 class="text-h2 font-semibold">
            {{ selected ?? '选择日志' }}
          </h2>
          <input
            v-model="search"
            placeholder="搜索"
            class="rounded-md border border-neutral-200 px-2 py-1 text-caption"
          >
        </div>
      </template>
      <Skeleton
        v-if="logQuery.isLoading.value && selected"
        height="200px"
      />
      <pre
        v-else-if="selected"
        class="max-h-[60vh] overflow-y-auto rounded-md bg-neutral-100 p-3 text-caption"
      >{{ filteredLines.join('\n') }}</pre>
      <p
        v-else
        class="text-body text-neutral-500"
      >
        在左侧选择一个日志文件
      </p>
    </Card>
  </div>
</template>
