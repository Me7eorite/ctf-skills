<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { apiClient, useApiQuery } from '@/composables/useApi'

interface Preset {
  name: string
  payload?: Record<string, unknown>
  created_at?: string
}

interface CategoryCard {
  id: 'web' | 'pwn' | 'reverse'
  label: string
  description: string
}

const categories: CategoryCard[] = [
  { id: 'web', label: 'Web', description: '面向 Web 漏洞与服务化题型' },
  { id: 'pwn', label: 'Pwn', description: '内存破坏与服务侧 Pwn' },
  { id: 'reverse', label: 'Reverse', description: '逆向与算法分析' },
]

const router = useRouter()
const selectedCategory = ref<CategoryCard['id']>('web')
const size = ref(5)
const submitting = ref(false)
const submitError = ref<string | null>(null)

const presets = useApiQuery<{ presets: Preset[] }>('presets', '/api/presets')

const previewPayload = computed(() => ({
  category: selectedCategory.value,
  size: size.value,
}))

function loadPreset(preset: Preset) {
  const payload = preset.payload as { category?: CategoryCard['id']; size?: number } | undefined
  if (!payload) return
  if (payload.category) selectedCategory.value = payload.category
  if (payload.size) size.value = payload.size
}

async function savePreset() {
  const name = window.prompt('Preset 名称')
  if (!name) return
  await apiClient.http('/api/presets', {
    method: 'POST',
    body: JSON.stringify({ name, payload: previewPayload.value }),
  })
  await presets.refetch()
}

async function submit() {
  submitting.value = true
  submitError.value = null
  try {
    const response = await apiClient.http<{ ok: boolean; shards?: string[]; message?: string }>(
      '/api/seeds/enqueue',
      {
        method: 'POST',
        body: JSON.stringify({ size: size.value }),
      },
    )
    const shard = response.shards?.[0]
    if (shard) router.push(`/generate/runs/${encodeURIComponent(shard)}`)
    else router.push('/generate/runs')
  } catch (err) {
    submitError.value = err instanceof Error ? err.message : String(err)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="grid grid-cols-1 gap-4 lg:grid-cols-[240px_1fr_320px]">
    <Card title="Saved presets">
      <Skeleton
        v-if="presets.isLoading.value"
        height="80px"
      />
      <EmptyState
        v-else-if="!presets.data.value?.presets?.length"
        title="还没有 preset"
        description="保存常用的组合以便复用"
      />
      <ul
        v-else
        class="space-y-1"
      >
        <li
          v-for="preset in presets.data.value.presets"
          :key="preset.name"
        >
          <button
            type="button"
            class="block w-full rounded-md px-2 py-1 text-left text-body hover:bg-neutral-100"
            @click="loadPreset(preset)"
          >
            {{ preset.name }}
          </button>
        </li>
      </ul>
    </Card>

    <Card title="选择类别">
      <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
        <button
          v-for="card in categories"
          :key="card.id"
          type="button"
          :class="[
            'rounded-md border p-3 text-left transition-colors',
            selectedCategory === card.id
              ? 'border-info-500 bg-info-50'
              : 'border-neutral-200 hover:bg-neutral-50',
          ]"
          @click="selectedCategory = card.id"
        >
          <p class="text-body font-semibold text-neutral-900">
            {{ card.label }}
          </p>
          <p class="text-caption text-neutral-500">
            {{ card.description }}
          </p>
        </button>
      </div>

      <div class="mt-4 flex items-center gap-3">
        <label class="text-body text-neutral-700">本次生成数量</label>
        <input
          v-model.number="size"
          type="number"
          min="1"
          max="20"
          class="w-20 rounded-md border border-neutral-200 px-2 py-1 text-body"
        >
        <Button
          variant="secondary"
          size="sm"
          @click="savePreset"
        >
          保存为 preset
        </Button>
      </div>
    </Card>

    <Card title="预览">
      <pre class="overflow-x-auto rounded-md bg-neutral-100 p-3 text-caption">{{
        JSON.stringify(previewPayload, null, 2)
      }}</pre>
      <div class="mt-3 flex items-center justify-between gap-3">
        <Badge tone="info">
          {{ size }} 个分片
        </Badge>
        <Button
          :disabled="submitting"
          @click="submit"
        >
          {{ submitting ? '提交中…' : '开始 Run' }}
        </Button>
      </div>
      <p
        v-if="submitError"
        class="mt-2 text-caption text-danger-600"
      >
        {{ submitError }}
      </p>
    </Card>
  </div>
</template>
