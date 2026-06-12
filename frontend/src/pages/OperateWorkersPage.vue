<script setup lang="ts">
import { ref } from 'vue'

import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { apiClient, useApiQuery } from '@/composables/useApi'

interface DashboardState {
  process?: {
    running: boolean
    kind?: string
    started_at?: string
    message?: string
    log?: string
  }
}

const query = useApiQuery<DashboardState>('state', '/api/state')
const acting = ref(false)
const lastMessage = ref<string | null>(null)

async function start(kind: 'worker' | 'validate') {
  acting.value = true
  lastMessage.value = null
  try {
    const response = await apiClient.http<{ ok: boolean; message: string }>(
      `/api/actions/${kind}`,
      { method: 'POST' },
    )
    lastMessage.value = response.message
  } catch (err) {
    lastMessage.value = err instanceof Error ? err.message : String(err)
  } finally {
    acting.value = false
    await query.refetch()
  }
}
</script>

<template>
  <Card title="Workers">
    <Skeleton
      v-if="query.isLoading.value"
      height="120px"
    />
    <div
      v-else
      class="space-y-3"
    >
      <div class="flex items-center gap-3 text-body">
        <Badge :tone="query.data.value?.process?.running ? 'success' : 'neutral'">
          {{ query.data.value?.process?.running ? '运行中' : '空闲' }}
        </Badge>
        <span
          v-if="query.data.value?.process?.kind"
          class="text-caption text-neutral-500"
        >
          {{ query.data.value.process.kind }}
        </span>
        <span
          v-if="query.data.value?.process?.started_at"
          class="text-caption text-neutral-400"
        >
          since {{ query.data.value.process.started_at }}
        </span>
      </div>
      <p
        v-if="query.data.value?.process?.message"
        class="text-caption text-neutral-600"
      >
        {{ query.data.value.process.message }}
      </p>
      <div class="flex gap-2">
        <Button
          :disabled="acting"
          @click="start('worker')"
        >
          启动 worker
        </Button>
        <Button
          variant="secondary"
          :disabled="acting"
          @click="start('validate')"
        >
          重新验证
        </Button>
      </div>
      <p
        v-if="lastMessage"
        class="text-caption text-neutral-700"
      >
        {{ lastMessage }}
      </p>
    </div>
  </Card>
</template>
