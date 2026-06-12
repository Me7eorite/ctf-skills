import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface LLMSettings {
  provider: string
  base_url: string
  model: string
  api_key_masked: string
}

export const useSettingsStore = defineStore('settings', () => {
  const llm = ref<LLMSettings | null>(null)

  function setLLM(payload: LLMSettings) {
    llm.value = payload
  }

  return { llm, setLLM }
})
