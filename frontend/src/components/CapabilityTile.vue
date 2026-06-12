<script setup lang="ts">
import { computed } from 'vue'

import Badge from '@/components/ui/Badge.vue'

interface Capability {
  id: string
  name: string
  status: 'enabled' | 'coming_soon' | 'disabled'
  description: string
  icon: string
  route: string
}

const props = defineProps<{ capability: Capability }>()

const tone = computed<'success' | 'neutral' | 'warning'>(() => {
  switch (props.capability.status) {
    case 'enabled':
      return 'success'
    case 'disabled':
      return 'warning'
    default:
      return 'neutral'
  }
})

const badgeLabel = computed(() => {
  switch (props.capability.status) {
    case 'enabled':
      return '可用'
    case 'disabled':
      return '已停用'
    default:
      return 'coming soon'
  }
})
</script>

<template>
  <router-link
    :to="capability.route"
    class="block rounded-lg border border-neutral-200 bg-white p-4 transition-colors hover:bg-neutral-50"
  >
    <div class="flex items-start justify-between">
      <div>
        <p class="text-h2 font-semibold text-neutral-900">
          {{ capability.name }}
        </p>
        <p class="mt-1 text-body text-neutral-500">
          {{ capability.description }}
        </p>
      </div>
      <Badge :tone="tone">
        {{ badgeLabel }}
      </Badge>
    </div>
  </router-link>
</template>
