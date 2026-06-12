<script setup lang="ts">
import { defineAsyncComponent, ref } from 'vue'

import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'

const MonacoViewer = defineAsyncComponent(() => import('@/components/MonacoViewer.vue'))

const content = ref('{\n  "profiles": []\n}\n')
const validationMessage = ref<string | null>(null)
const saving = ref(false)

function validate(text: string): { ok: boolean; message: string } {
  try {
    const parsed = JSON.parse(text)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return { ok: false, message: '根节点必须是对象' }
    }
    return { ok: true, message: 'JSON 结构有效' }
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) }
  }
}

function save() {
  const result = validate(content.value)
  validationMessage.value = result.message
  if (!result.ok) return
  saving.value = true
  // Real persistence will hit a future generation-profile endpoint; for now
  // we surface a confirmation so the operator can copy the validated JSON.
  saving.value = false
}
</script>

<template>
  <Card title="Generation Profile">
    <p class="mb-2 text-caption text-neutral-500">
      编辑 generation-profiles.json，保存前会做 JSON 结构校验。
    </p>
    <Suspense>
      <MonacoViewer
        :value="content"
        language="json"
        height="360px"
      />
    </Suspense>
    <div class="mt-3 flex items-center gap-3">
      <Button
        :disabled="saving"
        @click="save"
      >
        保存
      </Button>
      <span
        v-if="validationMessage"
        class="text-caption text-neutral-700"
      >
        {{ validationMessage }}
      </span>
    </div>
  </Card>
</template>
