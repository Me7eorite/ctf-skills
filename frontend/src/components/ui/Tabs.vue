<script setup lang="ts">
export interface TabItem {
  value: string
  label: string
}

const props = defineProps<{ modelValue: string; tabs: TabItem[] }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>()

function select(value: string) {
  if (value !== props.modelValue) emit('update:modelValue', value)
}
</script>

<template>
  <div>
    <div
      role="tablist"
      class="flex gap-2 border-b border-neutral-200"
    >
      <button
        v-for="tab in tabs"
        :key="tab.value"
        type="button"
        role="tab"
        :aria-selected="tab.value === modelValue"
        :class="[
          'px-3 py-2 text-body font-medium transition-colors',
          tab.value === modelValue
            ? 'border-b-2 border-info-600 text-info-700'
            : 'text-neutral-600 hover:text-neutral-900',
        ]"
        @click="select(tab.value)"
      >
        {{ tab.label }}
      </button>
    </div>
    <div class="pt-3">
      <slot :active="modelValue" />
    </div>
  </div>
</template>
