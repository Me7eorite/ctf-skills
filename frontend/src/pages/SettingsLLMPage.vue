<script setup lang="ts">
import { ref, watchEffect } from 'vue'

import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { apiClient, useApiQuery } from '@/composables/useApi'
import { useDirty } from '@/composables/useDirty'

interface LLMSettings {
  provider: string
  base_url: string
  model: string
  api_key_masked: string
}

interface TestResult {
  ok: boolean
  latency_ms: number
  model: string
  error: string | null
}

const providers = ['anthropic', 'openai', 'glm', 'custom']

const settings = useApiQuery<LLMSettings>('llm-settings', '/api/settings/llm')

const form = ref({
  provider: '',
  base_url: '',
  model: '',
  api_key: '',
})
const { isDirty, snapshot } = useDirty(form)
const showKey = ref(false)
const saving = ref(false)
const testing = ref(false)
const saveMessage = ref<string | null>(null)
const testResult = ref<TestResult | null>(null)

watchEffect(() => {
  const data = settings.data.value
  if (!data) return
  form.value = {
    provider: data.provider || 'anthropic',
    base_url: data.base_url,
    model: data.model,
    api_key: data.api_key_masked,
  }
  snapshot()
})

async function save() {
  saving.value = true
  saveMessage.value = null
  try {
    await apiClient.http('/api/settings/llm', {
      method: 'PUT',
      body: JSON.stringify(form.value),
    })
    await settings.refetch()
    saveMessage.value = '已保存'
  } catch (err) {
    saveMessage.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await apiClient.http<TestResult>('/api/settings/llm/test', {
      method: 'POST',
    })
  } catch (err) {
    testResult.value = {
      ok: false,
      latency_ms: 0,
      model: form.value.model,
      error: err instanceof Error ? err.message : String(err),
    }
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <Card title="LLM Provider">
    <Skeleton
      v-if="settings.isLoading.value"
      height="200px"
    />
    <form
      v-else
      class="space-y-3"
      @submit.prevent="save"
    >
      <label class="block text-body">
        <span class="text-caption text-neutral-600">Provider</span>
        <select
          v-model="form.provider"
          class="mt-1 block w-full rounded-md border border-neutral-200 px-2 py-1"
        >
          <option
            v-for="provider in providers"
            :key="provider"
            :value="provider"
          >
            {{ provider }}
          </option>
        </select>
      </label>
      <label class="block text-body">
        <span class="text-caption text-neutral-600">Base URL</span>
        <input
          v-model="form.base_url"
          type="url"
          class="mt-1 block w-full rounded-md border border-neutral-200 px-2 py-1"
        >
      </label>
      <label class="block text-body">
        <span class="text-caption text-neutral-600">Model</span>
        <input
          v-model="form.model"
          class="mt-1 block w-full rounded-md border border-neutral-200 px-2 py-1"
        >
      </label>
      <label class="block text-body">
        <span class="text-caption text-neutral-600">API key</span>
        <div class="mt-1 flex items-center gap-2">
          <input
            v-model="form.api_key"
            :type="showKey ? 'text' : 'password'"
            class="block w-full rounded-md border border-neutral-200 px-2 py-1 font-mono text-caption"
          >
          <Button
            type="button"
            variant="ghost"
            size="sm"
            @click="showKey = !showKey"
          >
            {{ showKey ? '隐藏' : '显示' }}
          </Button>
        </div>
        <p class="mt-1 text-caption text-neutral-400">
          留空或保留遮罩占位 ({{ form.api_key }}) 不会覆盖已存的 key
        </p>
      </label>

      <div class="flex items-center justify-between gap-3">
        <Button
          :disabled="!isDirty || saving"
          type="submit"
        >
          {{ saving ? '保存中…' : '保存' }}
        </Button>
        <Button
          variant="secondary"
          type="button"
          :disabled="testing"
          @click="testConnection"
        >
          {{ testing ? '测试中…' : 'Test connection' }}
        </Button>
      </div>
      <p
        v-if="saveMessage"
        class="text-caption text-neutral-700"
      >
        {{ saveMessage }}
      </p>

      <div
        v-if="testResult"
        class="rounded-md border border-neutral-200 p-3 text-body"
      >
        <Badge :tone="testResult.ok ? 'success' : 'danger'">
          {{ testResult.ok ? '连接成功' : '连接失败' }}
        </Badge>
        <p class="mt-1 text-caption text-neutral-600">
          model: {{ testResult.model || '—' }} · {{ testResult.latency_ms }} ms
        </p>
        <p
          v-if="testResult.error"
          class="mt-1 text-caption text-danger-600"
        >
          {{ testResult.error }}
        </p>
      </div>
    </form>
  </Card>
</template>
