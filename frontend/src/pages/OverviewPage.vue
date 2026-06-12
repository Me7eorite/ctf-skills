<script setup lang="ts">
import { computed } from 'vue'

import CapabilityTile from '@/components/CapabilityTile.vue'
import Badge from '@/components/ui/Badge.vue'
import Card from '@/components/ui/Card.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { useApiQuery } from '@/composables/useApi'

interface KPIs {
  total_challenges: number
  pass_rate: number
  avg_generation_minutes: number | null
  avg_quality_score: number | null
}

interface Capability {
  id: string
  name: string
  status: 'enabled' | 'coming_soon' | 'disabled'
  description: string
  icon: string
  route: string
}

interface RunRow {
  name: string
  state: string
  challenge_count: number
  pass_rate: number | null
}

interface DashboardState {
  process?: { running: boolean; kind?: string; message?: string }
}

const kpis = useApiQuery<KPIs>('kpis', '/api/kpis')
const capabilities = useApiQuery<Capability[]>('capabilities', '/api/capabilities')
const runs = useApiQuery<{ items: RunRow[] }>('runs-page', '/api/runs?limit=5')
const state = useApiQuery<DashboardState>('state', '/api/state')

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(1)}%`
}

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString()
}

const recent = computed(() => runs.data.value?.items ?? [])
const worker = computed(() => state.data.value?.process)
</script>

<template>
  <div class="space-y-4">
    <div class="grid grid-cols-1 gap-4 md:grid-cols-4">
      <Card title="题目总数">
        <Skeleton
          v-if="kpis.isLoading.value"
          height="32px"
        />
        <p
          v-else
          class="text-display font-semibold"
        >
          {{ formatNumber(kpis.data.value?.total_challenges) }}
        </p>
      </Card>
      <Card title="通过率">
        <Skeleton
          v-if="kpis.isLoading.value"
          height="32px"
        />
        <p
          v-else
          class="text-display font-semibold"
        >
          {{ formatPercent(kpis.data.value?.pass_rate) }}
        </p>
      </Card>
      <Card title="平均生成耗时">
        <Skeleton
          v-if="kpis.isLoading.value"
          height="32px"
        />
        <p
          v-else
          class="text-display font-semibold"
        >
          {{ kpis.data.value?.avg_generation_minutes ?? '—' }}
          <span
            v-if="kpis.data.value?.avg_generation_minutes"
            class="text-body text-neutral-500"
          >min</span>
        </p>
      </Card>
      <Card title="题面质量分">
        <Skeleton
          v-if="kpis.isLoading.value"
          height="32px"
        />
        <p
          v-else
          class="text-display font-semibold"
        >
          {{ kpis.data.value?.avg_quality_score ?? '—' }}
        </p>
      </Card>
    </div>

    <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Card title="最近 Runs">
        <Skeleton
          v-if="runs.isLoading.value"
          height="120px"
        />
        <EmptyState
          v-else-if="recent.length === 0"
          title="还没有任何 Run"
          description="启动一次 New Run 来生成第一批题目"
          cta-label="开始 Run"
          cta-to="/generate/new"
        />
        <ul
          v-else
          class="divide-y divide-neutral-100"
        >
          <li
            v-for="run in recent"
            :key="run.name"
            class="py-2 text-body"
          >
            <router-link
              :to="`/generate/runs/${encodeURIComponent(run.name)}`"
              class="flex items-center justify-between hover:text-info-700"
            >
              <span class="font-mono text-caption text-neutral-700">{{ run.name }}</span>
              <span class="flex items-center gap-2">
                <Badge
                  :tone="
                    run.state === 'done'
                      ? 'success'
                      : run.state === 'failed'
                        ? 'danger'
                        : 'info'
                  "
                >
                  {{ run.state }}
                </Badge>
                <span class="text-caption text-neutral-500">{{ run.challenge_count }} 题</span>
              </span>
            </router-link>
          </li>
        </ul>
      </Card>
      <Card title="Workers">
        <Skeleton
          v-if="state.isLoading.value"
          height="60px"
        />
        <div
          v-else-if="worker"
          class="space-y-1 text-body"
        >
          <p>
            <Badge :tone="worker.running ? 'success' : 'neutral'">
              {{ worker.running ? '运行中' : '空闲' }}
            </Badge>
            <span
              v-if="worker.kind"
              class="ml-2 text-caption text-neutral-500"
            >{{
              worker.kind
            }}</span>
          </p>
          <p
            v-if="worker.message"
            class="text-caption text-neutral-600"
          >
            {{ worker.message }}
          </p>
        </div>
      </Card>
    </div>

    <Card title="能力">
      <Skeleton
        v-if="capabilities.isLoading.value"
        height="120px"
      />
      <div
        v-else
        class="grid grid-cols-1 gap-4 md:grid-cols-2"
      >
        <CapabilityTile
          v-for="capability in capabilities.data.value ?? []"
          :key="capability.id"
          :capability="capability"
        />
      </div>
    </Card>
  </div>
</template>
