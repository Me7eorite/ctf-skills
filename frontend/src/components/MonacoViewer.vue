<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{ value: string; language?: string; height?: string }>(),
  { language: 'plaintext', height: '320px' },
)

const container = ref<HTMLElement | null>(null)
type MonacoEditor = { setValue: (value: string) => void; dispose: () => void }
let editor: MonacoEditor | null = null

onMounted(async () => {
  if (!container.value) return
  const monaco = await import('monaco-editor')
  editor = monaco.editor.create(container.value, {
    value: props.value,
    language: props.language,
    readOnly: true,
    minimap: { enabled: false },
    automaticLayout: true,
    fontFamily: 'JetBrains Mono, ui-monospace, SFMono-Regular',
    fontSize: 12,
    scrollBeyondLastLine: false,
  })
})

watch(
  () => props.value,
  (value) => {
    editor?.setValue(value)
  },
)

onBeforeUnmount(() => {
  editor?.dispose()
  editor = null
})
</script>

<template>
  <div
    ref="container"
    :style="{ height, border: '1px solid var(--neutral-200, #e5e5e5)' }"
  />
</template>
