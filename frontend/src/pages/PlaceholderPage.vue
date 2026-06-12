<script setup lang="ts">
import { computed } from 'vue'

import comingSoonUrl from '@/assets/empty-states/coming-soon.svg'
import Badge from '@/components/ui/Badge.vue'
import Card from '@/components/ui/Card.vue'

interface PlaceholderCopy {
  title: string
  description: string
  roadmap: string
}

const props = defineProps<{ capability: string }>()

const COPY: Record<string, PlaceholderCopy> = {
  'scenario-builder': {
    title: '情景生成',
    description: '装配多题情景包，串成连贯的攻防练习。',
    roadmap: 'Phase 2 后续：与题目生成器共享 metadata schema，先上情景骨架编辑器。',
  },
  'learning-materials': {
    title: '学习资料',
    description: '把题面、知识点、参考资料编排为可发布的讲义。',
    roadmap: 'Phase 2 后续：从已有 Run 抽取知识点抽屉，开放 Markdown 模板。',
  },
  'learning-paths': {
    title: '学习路线',
    description: '按难度与领域规划带依赖关系的学习路径。',
    roadmap: 'Phase 2 后续：以题目难度向量推导依赖图，导出 OPML 兼容格式。',
  },
  'quality-lint': {
    title: 'Quality · Lint',
    description: '题面、源码、solve 文件的可生成自动检查与签注。',
    roadmap: 'Phase 1：随 add-quality-lint 改动一起上线。当前后端尚未生成数据。',
  },
  'quality-diversity': {
    title: 'Quality · Diversity',
    description: '相同主题的题目之间的相似度度量与去重提示。',
    roadmap: 'Phase 1：随 add-quality-metrics 改动一起上线。当前后端尚未生成数据。',
  },
}

const copy = computed<PlaceholderCopy>(
  () =>
    COPY[props.capability] ?? {
      title: '即将开放',
      description: '该能力计划在后续 Phase 提供。',
      roadmap: '关注 OpenSpec 中的相关 change 进度。',
    },
)
</script>

<template>
  <Card>
    <div class="flex flex-col items-center gap-3 py-8 text-center">
      <img
        :src="comingSoonUrl"
        alt=""
        class="h-12 w-12"
      >
      <div>
        <h1 class="text-display font-semibold text-neutral-900">
          {{ copy.title }}
        </h1>
        <p class="mt-1 text-body text-neutral-500">
          {{ copy.description }}
        </p>
      </div>
      <Badge tone="accent">
        Roadmap
      </Badge>
      <p class="max-w-md text-caption text-neutral-600">
        {{ copy.roadmap }}
      </p>
    </div>
  </Card>
</template>
