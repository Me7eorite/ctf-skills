<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import Card from '@/components/ui/Card.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { apiClient, useApiQuery } from '@/composables/useApi'

type Category = 'web' | 'pwn' | 're'
type Difficulty = 'easy' | 'medium' | 'hard' | 'expert'

interface Seed {
  id: string
  title: string
  category: Category
  difficulty: Difficulty
  points: number
  port?: number
  primary_technique: string
  learning_objective: string
  runtime?: string
  framework?: string
}

interface DashboardState {
  seeds: Seed[]
  process?: {
    running: boolean
    kind?: string
    message?: string
    log?: string
  }
}

interface CreateRunResponse {
  ok: boolean
  message: string
  seeds: string[]
  shards: string[]
  worker: {
    requested: boolean
    started: boolean
    message: string
    dry_run?: boolean
  }
}

interface FormState {
  id: string
  title: string
  category: Category
  difficulty: Difficulty
  points: number
  port: number
  primary_technique: string
  learning_objective: string
  runtime: string
  framework: string
  shard_size: number
  start_worker: boolean
  dry_run: boolean
}

const templates: Record<Category, Omit<FormState, 'shard_size' | 'start_worker' | 'dry_run'>> = {
  web: {
    id: 'web-0001',
    title: 'Session Trust',
    category: 'web',
    difficulty: 'easy',
    points: 100,
    port: 8080,
    primary_technique: 'cookie auth bypass',
    learning_objective: '理解服务端信任边界被用户可控会话字段破坏时的风险',
    runtime: 'node',
    framework: 'Express',
  },
  pwn: {
    id: 'pwn-0001',
    title: 'Stack Note',
    category: 'pwn',
    difficulty: 'easy',
    points: 100,
    port: 9001,
    primary_technique: 'stack overflow',
    learning_objective: '理解基础栈溢出如何控制返回地址',
    runtime: 'c',
    framework: 'ELF service',
  },
  re: {
    id: 're-0001',
    title: 'Hidden Routine',
    category: 're',
    difficulty: 'easy',
    points: 100,
    port: 8080,
    primary_technique: 'string recovery',
    learning_objective: '理解如何从二进制常量和控制流中恢复 flag',
    runtime: 'c',
    framework: 'ELF',
  },
}

const difficulties: Difficulty[] = ['easy', 'medium', 'hard', 'expert']
const state = useApiQuery<DashboardState>('state', '/api/state')
const form = reactive<FormState>({
  ...templates.web,
  shard_size: 1,
  start_worker: true,
  dry_run: true,
})

const saving = ref(false)
const submitting = ref(false)
const message = ref<string | null>(null)
const error = ref<string | null>(null)
const result = ref<CreateRunResponse | null>(null)

const needsPort = computed(() => form.category === 'web' || form.category === 'pwn')
const savedSeeds = computed(() => state.data.value?.seeds ?? [])
const workerStatus = computed(() => state.data.value?.process)

const seedPayload = computed<Seed>(() => {
  const seed: Seed = {
    id: form.id.trim(),
    title: form.title.trim(),
    category: form.category,
    difficulty: form.difficulty,
    points: form.points,
    primary_technique: form.primary_technique.trim(),
    learning_objective: form.learning_objective.trim(),
  }
  if (needsPort.value) seed.port = form.port
  if (form.runtime.trim()) seed.runtime = form.runtime.trim()
  if (form.framework.trim()) seed.framework = form.framework.trim()
  return seed
})

function applyTemplate(category: Category) {
  Object.assign(form, templates[category])
}

function loadSeed(seed: Seed) {
  Object.assign(form, {
    id: seed.id,
    title: seed.title,
    category: seed.category,
    difficulty: seed.difficulty,
    points: seed.points,
    port: seed.port ?? 8080,
    primary_technique: seed.primary_technique,
    learning_objective: seed.learning_objective,
    runtime: seed.runtime ?? '',
    framework: seed.framework ?? '',
  })
  message.value = `已载入 ${seed.id}`
  error.value = null
}

function clearFeedback() {
  message.value = null
  error.value = null
  result.value = null
}

async function saveSeed() {
  clearFeedback()
  saving.value = true
  try {
    const response = await apiClient.http<{ ok: boolean; seed: Seed }>('/api/seeds', {
      method: 'POST',
      body: JSON.stringify(seedPayload.value),
    })
    message.value = `Seed 已保存：${response.seed.id}`
    await state.refetch()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}

async function createRun() {
  clearFeedback()
  submitting.value = true
  try {
    const response = await apiClient.http<CreateRunResponse>('/api/runs', {
      method: 'POST',
      body: JSON.stringify({
        seeds: [seedPayload.value],
        shard_size: form.shard_size,
        start_worker: form.start_worker,
        dry_run: form.dry_run,
      }),
    })
    result.value = response
    message.value = `${response.message}；${response.worker.message}`
    await state.refetch()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <Card>
      <template #header>
        <div class="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 class="text-h2 font-semibold text-neutral-900">
              新建题目生成
            </h2>
            <p class="text-caption text-neutral-500">
              填一个 seed，保存后创建分片；默认使用 dry-run 测试队列和 worker 流程。
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <Badge :tone="workerStatus?.running ? 'success' : 'neutral'">
              {{ workerStatus?.running ? 'worker 运行中' : 'worker 空闲' }}
            </Badge>
            <Badge tone="info">
              {{ form.dry_run ? 'dry-run' : 'real run' }}
            </Badge>
          </div>
        </div>
      </template>

      <div class="grid gap-3 md:grid-cols-3">
        <button
          v-for="template in Object.values(templates)"
          :key="template.category"
          type="button"
          :class="[
            'rounded-md border p-3 text-left transition-colors',
            form.category === template.category
              ? 'border-info-500 bg-info-50'
              : 'border-neutral-200 hover:bg-neutral-50',
          ]"
          @click="applyTemplate(template.category)"
        >
          <span class="text-body font-semibold text-neutral-900">
            {{ template.category === 're' ? 'Reverse' : template.category.toUpperCase() }}
          </span>
          <span class="mt-1 block text-caption text-neutral-500">
            {{ template.title }} · {{ template.primary_technique }}
          </span>
        </button>
      </div>
    </Card>

    <div class="grid gap-4 lg:grid-cols-[1fr_320px]">
      <form
        class="space-y-4"
        @submit.prevent="createRun"
      >
        <Card
          title="1. Seed 信息"
          description="这些字段会保存到 work/challenge_seeds.json，并写入 pending shard。"
        >
          <div class="grid gap-3 md:grid-cols-2">
            <label class="space-y-1">
              <span class="text-caption font-medium text-neutral-600">题目 ID</span>
              <input
                v-model.trim="form.id"
                class="w-full rounded-md border border-neutral-200 px-2 py-1 text-body"
                required
              >
            </label>
            <label class="space-y-1">
              <span class="text-caption font-medium text-neutral-600">标题</span>
              <input
                v-model.trim="form.title"
                class="w-full rounded-md border border-neutral-200 px-2 py-1 text-body"
                required
              >
            </label>
            <label class="space-y-1">
              <span class="text-caption font-medium text-neutral-600">类别</span>
              <select
                v-model="form.category"
                class="w-full rounded-md border border-neutral-200 px-2 py-1 text-body"
                @change="applyTemplate(form.category)"
              >
                <option value="web">
                  Web
                </option>
                <option value="pwn">
                  Pwn
                </option>
                <option value="re">
                  Reverse
                </option>
              </select>
            </label>
            <label class="space-y-1">
              <span class="text-caption font-medium text-neutral-600">难度</span>
              <select
                v-model="form.difficulty"
                class="w-full rounded-md border border-neutral-200 px-2 py-1 text-body"
              >
                <option
                  v-for="difficulty in difficulties"
                  :key="difficulty"
                  :value="difficulty"
                >
                  {{ difficulty }}
                </option>
              </select>
            </label>
            <label class="space-y-1">
              <span class="text-caption font-medium text-neutral-600">分值</span>
              <input
                v-model.number="form.points"
                type="number"
                min="1"
                class="w-full rounded-md border border-neutral-200 px-2 py-1 text-body"
                required
              >
            </label>
            <label
              v-if="needsPort"
              class="space-y-1"
            >
              <span class="text-caption font-medium text-neutral-600">服务端口</span>
              <input
                v-model.number="form.port"
                type="number"
                min="1"
                max="65535"
                class="w-full rounded-md border border-neutral-200 px-2 py-1 text-body"
                required
              >
            </label>
            <label class="space-y-1">
              <span class="text-caption font-medium text-neutral-600">运行时</span>
              <input
                v-model.trim="form.runtime"
                class="w-full rounded-md border border-neutral-200 px-2 py-1 text-body"
              >
            </label>
            <label class="space-y-1">
              <span class="text-caption font-medium text-neutral-600">框架 / 格式</span>
              <input
                v-model.trim="form.framework"
                class="w-full rounded-md border border-neutral-200 px-2 py-1 text-body"
              >
            </label>
            <label class="space-y-1 md:col-span-2">
              <span class="text-caption font-medium text-neutral-600">主要技术点</span>
              <input
                v-model.trim="form.primary_technique"
                class="w-full rounded-md border border-neutral-200 px-2 py-1 text-body"
                required
              >
            </label>
            <label class="space-y-1 md:col-span-2">
              <span class="text-caption font-medium text-neutral-600">学习目标</span>
              <textarea
                v-model.trim="form.learning_objective"
                rows="3"
                class="w-full resize-none rounded-md border border-neutral-200 px-2 py-1 text-body"
                required
              />
            </label>
          </div>
        </Card>

        <Card
          title="2. 生成方式"
          description="dry-run 会创建并领取分片、写日志，然后把分片放回 pending，适合先验证流程。"
        >
          <div class="flex flex-wrap items-end gap-4">
            <label class="space-y-1">
              <span class="text-caption font-medium text-neutral-600">Shard size</span>
              <input
                v-model.number="form.shard_size"
                type="number"
                min="1"
                max="20"
                class="w-24 rounded-md border border-neutral-200 px-2 py-1 text-body"
              >
            </label>
            <label class="flex items-center gap-2 text-body text-neutral-700">
              <input
                v-model="form.start_worker"
                type="checkbox"
                class="h-4 w-4 rounded border-neutral-300"
              >
              创建分片后启动单 worker
            </label>
            <label class="flex items-center gap-2 text-body text-neutral-700">
              <input
                v-model="form.dry_run"
                type="checkbox"
                class="h-4 w-4 rounded border-neutral-300"
              >
              dry-run 测试流程
            </label>
          </div>
        </Card>

        <Card title="3. 执行">
          <div class="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              :disabled="saving || submitting"
              @click="saveSeed"
            >
              保存 Seed
            </Button>
            <Button
              type="submit"
              :disabled="saving || submitting"
            >
              {{ submitting ? '提交中…' : '创建分片并测试运行' }}
            </Button>
          </div>
          <p class="mt-2 text-caption text-neutral-500">
            提交会先保存当前 seed，再创建 pending shard。开启 worker 时默认使用 dry-run，不会调用真实 Hermes。
          </p>
          <p
            v-if="error"
            class="mt-3 rounded-md border border-danger-200 bg-danger-50 p-2 text-caption text-danger-700"
          >
            {{ error }}
          </p>
          <p
            v-if="message"
            class="mt-3 rounded-md border border-success-200 bg-success-50 p-2 text-caption text-success-700"
          >
            {{ message }}
          </p>
        </Card>
      </form>

      <aside class="space-y-4">
        <Card title="已保存 Seeds">
          <Skeleton
            v-if="state.isLoading.value"
            height="88px"
          />
          <EmptyState
            v-else-if="savedSeeds.length === 0"
            title="还没有 Seed"
            description="先保存当前模板，之后可以从这里快速载入"
          />
          <ul
            v-else
            class="space-y-2"
          >
            <li
              v-for="seed in savedSeeds"
              :key="seed.id"
              class="rounded-md border border-neutral-200 p-2"
            >
              <button
                type="button"
                class="block w-full text-left"
                @click="loadSeed(seed)"
              >
                <span class="font-mono text-caption text-info-700">{{ seed.id }}</span>
                <span class="block text-body font-medium text-neutral-900">{{ seed.title }}</span>
                <span class="text-caption text-neutral-500">
                  {{ seed.category }} · {{ seed.difficulty }} · {{ seed.primary_technique }}
                </span>
              </button>
            </li>
          </ul>
        </Card>

        <Card title="本次结果">
          <EmptyState
            v-if="!result"
            title="等待提交"
            description="创建后会显示 shard、worker 和日志入口"
          />
          <div
            v-else
            class="space-y-3 text-body"
          >
            <div>
              <p class="text-caption font-medium text-neutral-500">
                Shards
              </p>
              <ul class="mt-1 space-y-1">
                <li
                  v-for="shard in result.shards"
                  :key="shard"
                  class="font-mono text-caption text-neutral-800"
                >
                  {{ shard }}
                </li>
              </ul>
            </div>
            <div>
              <p class="text-caption font-medium text-neutral-500">
                Worker
              </p>
              <Badge :tone="result.worker.started ? 'success' : 'neutral'">
                {{ result.worker.started ? '已执行' : '未启动' }}
              </Badge>
              <p class="mt-1 text-caption text-neutral-600">
                {{ result.worker.message }}
              </p>
            </div>
            <div class="flex flex-wrap gap-2">
              <router-link
                class="text-caption font-medium text-info-700 hover:underline"
                to="/generate/runs"
              >
                查看 Runs
              </router-link>
              <router-link
                class="text-caption font-medium text-info-700 hover:underline"
                to="/operate/queue"
              >
                查看 Queue
              </router-link>
              <router-link
                v-if="workerStatus?.log"
                class="text-caption font-medium text-info-700 hover:underline"
                to="/operate/logs"
              >
                查看日志
              </router-link>
            </div>
          </div>
        </Card>
      </aside>
    </div>
  </div>
</template>
