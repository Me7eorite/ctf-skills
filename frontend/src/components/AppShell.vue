<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { useUIStore } from '@/stores/ui'

interface NavItem {
  label: string
  to: string
  badge?: string
}

interface NavGroup {
  label: string
  items: NavItem[]
}

const ui = useUIStore()
const route = useRoute()

const groups: NavGroup[] = [
  { label: 'Overview', items: [{ label: 'Overview', to: '/' }] },
  {
    label: 'Generate',
    items: [
      { label: 'New Run', to: '/generate/new' },
      { label: 'Runs', to: '/generate/runs' },
    ],
  },
  {
    label: 'Scenario',
    items: [{ label: 'Builder', to: '/scenario', badge: 'coming soon' }],
  },
  {
    label: 'Learning',
    items: [
      { label: 'Materials', to: '/learning/materials', badge: 'coming soon' },
      { label: 'Paths', to: '/learning/paths', badge: 'coming soon' },
    ],
  },
  {
    label: 'Operate',
    items: [
      { label: 'Queue', to: '/operate/queue' },
      { label: 'Workers', to: '/operate/workers' },
      { label: 'Logs', to: '/operate/logs' },
    ],
  },
  {
    label: 'Quality',
    items: [
      { label: 'Lint', to: '/quality/lint', badge: 'phase 1' },
      { label: 'Diversity', to: '/quality/diversity', badge: 'phase 1' },
    ],
  },
  {
    label: 'Settings',
    items: [
      { label: 'LLM Provider', to: '/settings/llm' },
      { label: 'Generation Profile', to: '/settings/profile' },
    ],
  },
]

const breadcrumb = computed<string>(() => {
  const meta = route.meta?.breadcrumb
  return typeof meta === 'string' ? meta : 'Console'
})
</script>

<template>
  <div class="flex min-h-screen bg-neutral-50">
    <aside class="w-60 border-r border-neutral-200 bg-white p-4">
      <div class="mb-4 flex items-center gap-2">
        <div class="h-6 w-6 rounded-md bg-accent-500" />
        <span class="text-h2 font-semibold text-neutral-900">Challenge Factory</span>
      </div>
      <nav class="space-y-4">
        <div
          v-for="group in groups"
          :key="group.label"
        >
          <p class="mb-2 text-caption font-semibold uppercase tracking-wide text-neutral-500">
            {{ group.label }}
          </p>
          <ul class="space-y-1">
            <li
              v-for="item in group.items"
              :key="item.to"
            >
              <router-link
                :to="item.to"
                class="flex items-center justify-between rounded-md px-2 py-1 text-body text-neutral-700 hover:bg-neutral-100"
                :class="{ 'bg-neutral-100 font-medium text-neutral-900': route.path === item.to }"
              >
                <span>{{ item.label }}</span>
                <span
                  v-if="item.badge"
                  class="rounded-sm bg-neutral-200 px-2 text-caption text-neutral-600"
                >
                  {{ item.badge }}
                </span>
              </router-link>
            </li>
          </ul>
        </div>
      </nav>
    </aside>
    <main class="flex-1">
      <header
        class="flex h-12 items-center justify-between border-b border-neutral-200 bg-white px-4"
      >
        <div class="flex items-center gap-3">
          <span class="text-caption text-neutral-500">Default</span>
          <span class="text-caption text-neutral-300">/</span>
          <span class="text-body font-medium text-neutral-900">{{ breadcrumb }}</span>
        </div>
        <div class="flex items-center gap-2">
          <button
            type="button"
            class="inline-flex h-6 items-center gap-2 rounded-md border border-neutral-200 px-3 text-caption text-neutral-600 hover:bg-neutral-100"
            @click="ui.openCommand()"
          >
            <span>跳转</span>
            <kbd class="rounded-sm bg-neutral-200 px-2 font-mono text-caption">⌘K</kbd>
          </button>
        </div>
      </header>
      <section class="p-4">
        <slot />
      </section>
    </main>
  </div>
</template>
