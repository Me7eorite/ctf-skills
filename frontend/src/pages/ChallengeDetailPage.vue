<script setup lang="ts">
import { computed, defineAsyncComponent, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Card from '@/components/ui/Card.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import Tabs, { type TabItem } from '@/components/ui/Tabs.vue'
import { useApiQuery } from '@/composables/useApi'

const MonacoViewer = defineAsyncComponent(() => import('@/components/MonacoViewer.vue'))

interface ChallengeDetail {
  id: string
  metadata: Record<string, unknown>
  files: string[]
  validation: Record<string, unknown>
}

const props = defineProps<{ shard: string; id: string }>()

const tabs: TabItem[] = [
  { value: 'brief', label: 'Brief' },
  { value: 'source', label: 'Source' },
  { value: 'solve', label: 'Solve' },
  { value: 'verify', label: 'Verify' },
  { value: 'quality', label: 'Quality' },
  { value: 'telemetry', label: 'Telemetry' },
]

const route = useRoute()
const router = useRouter()

const activeTab = computed<string>({
  get: () => {
    const value = route.query.tab
    const candidate = Array.isArray(value) ? value[0] : value
    return typeof candidate === 'string' && tabs.some((t) => t.value === candidate)
      ? candidate
      : 'brief'
  },
  set: (next: string) => {
    router.replace({ query: { ...route.query, tab: next === 'brief' ? undefined : next } })
  },
})

const detail = useApiQuery<ChallengeDetail>(
  ['challenge-detail', props.shard, props.id],
  `/api/runs/${encodeURIComponent(props.shard)}/challenges/${encodeURIComponent(props.id)}`,
)

watch(
  () => [props.shard, props.id],
  () => detail.refetch(),
)

function pickFile(suffixes: string[]): string | null {
  const files = detail.data.value?.files ?? []
  return files.find((path) => suffixes.some((suffix) => path.endsWith(suffix))) ?? null
}

const briefFile = computed(() => pickFile(['brief.md', 'metadata.json']))
const sourceFile = computed(() => pickFile(['main.py', 'app.py', 'server.py', 'index.js']))
const solveFile = computed(() => pickFile(['solve/solve.py', 'solve.py']))

function buildArtifactUrl(file: string): string {
  const category = String(detail.data.value?.metadata.category ?? 'web')
  return `/api/runs/${encodeURIComponent(props.shard)}/artifacts/${category}/${encodeURIComponent(props.id)}/${file
    .split('/')
    .map(encodeURIComponent)
    .join('/')}`
}

const fileContents = useApiQuery<string>(
  computed(() => ['file', briefFile.value ?? sourceFile.value ?? solveFile.value ?? '', activeTab.value]) as never,
  computed(() => {
    const candidate =
      activeTab.value === 'source'
        ? sourceFile.value
        : activeTab.value === 'solve'
          ? solveFile.value
          : briefFile.value
    return candidate ? buildArtifactUrl(candidate) : '/api/state'
  }).value,
)

function languageForFile(file: string | null): string {
  if (!file) return 'plaintext'
  if (file.endsWith('.py')) return 'python'
  if (file.endsWith('.md')) return 'markdown'
  if (file.endsWith('.json')) return 'json'
  if (file.endsWith('.yml') || file.endsWith('.yaml')) return 'yaml'
  if (file.endsWith('.dockerfile') || file.endsWith('Dockerfile')) return 'dockerfile'
  return 'plaintext'
}
</script>

<template>
  <div class="space-y-4">
    <header>
      <h1 class="text-display font-semibold">
        {{ id }}
      </h1>
      <p class="text-caption text-neutral-500">
        in run
        <router-link
          :to="`/generate/runs/${encodeURIComponent(shard)}`"
          class="text-info-700 hover:underline"
        >
          {{ shard }}
        </router-link>
      </p>
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
            v-if="active === 'brief'"
            title="题面"
          >
            <p
              v-if="!briefFile"
              class="text-body text-neutral-500"
            >
              未找到题面文件
            </p>
            <Suspense v-else>
              <MonacoViewer
                :value="typeof fileContents.data.value === 'string' ? fileContents.data.value : ''"
                :language="languageForFile(briefFile)"
              />
            </Suspense>
          </Card>
          <Card
            v-else-if="active === 'source'"
            title="Source"
          >
            <p
              v-if="!sourceFile"
              class="text-body text-neutral-500"
            >
              未找到源码文件
            </p>
            <Suspense v-else>
              <MonacoViewer
                :value="typeof fileContents.data.value === 'string' ? fileContents.data.value : ''"
                :language="languageForFile(sourceFile)"
              />
            </Suspense>
          </Card>
          <Card
            v-else-if="active === 'solve'"
            title="Solve"
          >
            <p
              v-if="!solveFile"
              class="text-body text-neutral-500"
            >
              未找到 solve 文件
            </p>
            <Suspense v-else>
              <MonacoViewer
                :value="typeof fileContents.data.value === 'string' ? fileContents.data.value : ''"
                :language="languageForFile(solveFile)"
              />
            </Suspense>
          </Card>
          <Card
            v-else-if="active === 'verify'"
            title="Validation"
          >
            <pre class="overflow-x-auto rounded-md bg-neutral-100 p-3 text-caption">{{
              JSON.stringify(detail.data.value?.validation ?? {}, null, 2)
            }}</pre>
          </Card>
          <Card
            v-else-if="active === 'quality'"
            title="Quality"
          >
            <p class="text-body text-neutral-500">
              质量评分在 Phase 1 后启用。
            </p>
          </Card>
          <Card
            v-else
            title="Telemetry"
          >
            <pre class="overflow-x-auto rounded-md bg-neutral-100 p-3 text-caption">{{
              JSON.stringify(detail.data.value?.metadata ?? {}, null, 2)
            }}</pre>
          </Card>
        </template>
      </template>
    </Tabs>
  </div>
</template>
