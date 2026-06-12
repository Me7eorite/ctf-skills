<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Badge from '@/components/ui/Badge.vue'
import Card from '@/components/ui/Card.vue'
import GanttRow from '@/components/ui/GanttRow.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import Tabs, { type TabItem } from '@/components/ui/Tabs.vue'
import { useApiQuery } from '@/composables/useApi'

interface ChallengeReport {
  id?: string
  challenge_id?: string
  solve_status?: string
  validation_status?: string
  validation_elapsed?: number
}

interface RunDetail {
  name: string
  state: string
  started_at: number
  challenge_ids: string[]
  pass_rate: number | null
  categories: string[]
  report?: { challenges?: ChallengeReport[] }
}

const props = defineProps<{ shard: string }>()
const route = useRoute()
const router = useRouter()

const tabs: TabItem[] = [
  { value: 'overview', label: 'Overview' },
  { value: 'challenges', label: 'Challenges' },
  { value: 'artifacts', label: 'Artifacts' },
  { value: 'validation', label: 'Validation' },
  { value: 'logs', label: 'Logs' },
  { value: 'settings', label: 'Settings' },
]

const activeTab = computed<string>({
  get: () => {
    const value = route.query.tab
    const candidate = Array.isArray(value) ? value[0] : value
    return typeof candidate === 'string' && tabs.some((t) => t.value === candidate)
      ? candidate
      : 'overview'
  },
  set: (next: string) => {
    router.replace({ query: { ...route.query, tab: next === 'overview' ? undefined : next } })
  },
})

const detail = useApiQuery<RunDetail>(
  ['run-detail', props.shard],
  `/api/runs/${encodeURIComponent(props.shard)}`,
)

watch(
  () => props.shard,
  () => detail.refetch(),
)

const challenges = computed(() => detail.data.value?.report?.challenges ?? [])

const ganttSegments = computed(() => {
  return challenges.value.map((entry, index) => ({
    start: index,
    end: index + 1,
    label: entry.id ?? entry.challenge_id ?? `c-${index}`,
    tone: (entry.solve_status === 'passed' ? 'success' : 'danger') as 'success' | 'danger',
  }))
})
</script>

<template>
  <div class="space-y-4">
    <header class="flex items-center justify-between">
      <div>
        <h1 class="text-display font-semibold">
          {{ shard }}
        </h1>
        <p
          v-if="detail.data.value"
          class="text-caption text-neutral-500"
        >
          {{ detail.data.value.challenge_ids.length }} 题 ·
          {{ detail.data.value.categories.join(', ') }}
        </p>
      </div>
      <Badge
        v-if="detail.data.value"
        tone="info"
      >
        {{ detail.data.value.state }}
      </Badge>
    </header>

    <Tabs
      v-model="activeTab"
      :tabs="tabs"
    >
      <template #default="{ active }">
        <Skeleton
          v-if="detail.isLoading.value"
          height="200px"
        />
        <template v-else>
          <Card
            v-if="active === 'overview'"
            title="Timeline"
          >
            <GanttRow
              v-if="ganttSegments.length"
              :segments="ganttSegments"
              :range-start="0"
              :range-end="Math.max(1, ganttSegments.length)"
              label="challenges"
            />
            <p
              v-else
              class="text-body text-neutral-500"
            >
              暂无时序数据
            </p>
          </Card>

          <Card
            v-else-if="active === 'challenges'"
            title="Challenges"
          >
            <ul class="divide-y divide-neutral-100">
              <li
                v-for="challenge in challenges"
                :key="challenge.id ?? challenge.challenge_id"
                class="flex items-center justify-between py-2 text-body"
              >
                <router-link
                  :to="`/generate/runs/${encodeURIComponent(shard)}/challenges/${challenge.id ?? challenge.challenge_id}`"
                  class="font-mono text-caption text-info-700 hover:underline"
                >
                  {{ challenge.id ?? challenge.challenge_id }}
                </router-link>
                <Badge :tone="challenge.solve_status === 'passed' ? 'success' : 'danger'">
                  {{ challenge.validation_status ?? challenge.solve_status }}
                </Badge>
              </li>
            </ul>
          </Card>

          <Card
            v-else-if="active === 'artifacts'"
            title="Artifacts"
          >
            <p class="text-body text-neutral-500">
              使用 GET /api/runs/{{ shard }}/artifacts/&lt;path&gt; 抓取产物。
            </p>
          </Card>

          <Card
            v-else-if="active === 'validation'"
            title="Validation"
          >
            <pre class="overflow-x-auto rounded-md bg-neutral-100 p-3 text-caption">{{
              JSON.stringify(detail.data.value?.report ?? {}, null, 2)
            }}</pre>
          </Card>

          <Card
            v-else-if="active === 'logs'"
            title="Logs"
          >
            <p class="text-body text-neutral-500">
              日志聚合见 Operate · Logs 页面。
            </p>
          </Card>

          <Card
            v-else
            title="Settings"
          >
            <p class="text-body text-neutral-500">
              本 Run 使用全局 LLM 配置；当前不支持单 Run 覆盖。
            </p>
          </Card>
        </template>
      </template>
    </Tabs>
  </div>
</template>
