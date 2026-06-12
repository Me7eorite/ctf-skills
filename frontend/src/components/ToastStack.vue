<script setup lang="ts">
import { onMounted } from 'vue'

import Toast from '@/components/ui/Toast.vue'
import { useEventStream } from '@/composables/useEventStream'
import { useNotificationsStore } from '@/stores/notifications'

interface ProgressEvent {
  shard?: string
  stage?: string
  status?: string
  message?: string
}

const notifications = useNotificationsStore()
const stream = useEventStream((event) => {
  let payload: ProgressEvent
  try {
    payload = JSON.parse(event.data ?? '{}') as ProgressEvent
  } catch {
    return
  }
  if (!payload?.status) return
  if (payload.status === 'failed') {
    notifications.push({
      tone: 'danger',
      title: `${payload.shard ?? 'shard'} 失败 · ${payload.stage ?? ''}`,
      description: payload.message,
    })
    return
  }
  if (payload.status === 'passed' && payload.stage === 'complete') {
    notifications.push({
      tone: 'success',
      title: `${payload.shard ?? 'shard'} 完成`,
      description: payload.message,
    })
  }
})

onMounted(() => {
  stream.open('/api/events/stream?replay=false')
})
</script>

<template>
  <div class="pointer-events-none fixed bottom-4 right-4 z-40 flex w-80 flex-col gap-2">
    <transition-group
      name="sheet"
      tag="div"
      class="flex flex-col gap-2"
    >
      <div
        v-for="entry in notifications.items.slice(0, 4)"
        :key="entry.id"
        class="pointer-events-auto"
      >
        <Toast
          :tone="entry.tone"
          :title="entry.title"
          :description="entry.description"
        />
      </div>
    </transition-group>
  </div>
</template>
