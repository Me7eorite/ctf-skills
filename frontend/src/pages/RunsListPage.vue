<script setup lang="ts">
import { computed, ref } from 'vue'

import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { useApiQuery } from '@/composables/useApi'

interface RunRow {
  name: string
  state: 'pending' | 'running' | 'done' | 'failed'
  started_at: number
  challenge_count: number
  pass_rate: number | null
  categories: string[]
}

const limit = 20
const page = ref(0)

const url = computed(() => `/api/runs?limit=${limit}&offset=${page.value * limit}`)
const query = useApiQuery<{ items: RunRow[]; total: number }>(['runs-page', page], url.value)

function nextPage() {
  page.value += 1
  query.refetch()
}

function prevPage() {
  if (page.value === 0) return
  page.value -= 1
  query.refetch()
}

function toneFor(state: RunRow['state']): 'success' | 'danger' | 'info' | 'neutral' {
  switch (state) {
    case 'done':
      return 'success'
    case 'failed':
      return 'danger'
    case 'running':
      return 'info'
    default:
      return 'neutral'
  }
}

function fmtRate(value: number | null): string {
  if (value === null) return '—'
  return `${Math.round(value * 100)}%`
}

const items = computed(() => query.data.value?.items ?? [])
const total = computed(() => query.data.value?.total ?? 0)
</script>

<template>
  <Card>
    <template #header>
      <div class="flex items-center justify-between">
        <div>
          <h2 class="text-h2 font-semibold">
            Runs
          </h2>
          <p class="text-caption text-neutral-500">
            {{ total }} 条记录
          </p>
        </div>
        <div class="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            :disabled="page === 0"
            @click="prevPage"
          >
            上一页
          </Button>
          <Button
            variant="secondary"
            size="sm"
            :disabled="(page + 1) * limit >= total"
            @click="nextPage"
          >
            下一页
          </Button>
        </div>
      </div>
    </template>
    <Skeleton
      v-if="query.isLoading.value"
      height="200px"
    />
    <EmptyState
      v-else-if="items.length === 0"
      title="还没有 Run"
      description="开始第一次 Run 来生成题目"
      cta-label="Start your first run"
      cta-to="/generate/new"
    />
    <table
      v-else
      class="w-full text-body"
    >
      <thead class="border-b border-neutral-200 text-caption uppercase text-neutral-500">
        <tr>
          <th class="py-2 text-left">
            Shard
          </th>
          <th class="py-2 text-left">
            State
          </th>
          <th class="py-2 text-left">
            题数
          </th>
          <th class="py-2 text-left">
            类别
          </th>
          <th class="py-2 text-left">
            通过率
          </th>
        </tr>
      </thead>
      <tbody class="divide-y divide-neutral-100">
        <tr
          v-for="run in items"
          :key="run.name"
        >
          <td class="py-2 font-mono text-caption">
            <router-link
              :to="`/generate/runs/${encodeURIComponent(run.name)}`"
              class="text-info-700 hover:underline"
            >
              {{ run.name }}
            </router-link>
          </td>
          <td class="py-2">
            <Badge :tone="toneFor(run.state)">
              {{ run.state }}
            </Badge>
          </td>
          <td class="py-2">
            {{ run.challenge_count }}
          </td>
          <td class="py-2 text-caption text-neutral-600">
            {{ run.categories.join(', ') }}
          </td>
          <td class="py-2">
            {{ fmtRate(run.pass_rate) }}
          </td>
        </tr>
      </tbody>
    </table>
  </Card>
</template>
