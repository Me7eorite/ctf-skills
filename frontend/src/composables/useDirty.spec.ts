import { ref } from 'vue'
import { describe, expect, it } from 'vitest'

import { useDirty } from './useDirty'

describe('useDirty (LLM Settings form pattern)', () => {
  it('starts clean and turns dirty when a tracked field changes', () => {
    const form = ref({
      provider: 'anthropic',
      base_url: 'https://api.anthropic.com',
      model: 'claude-3-5-sonnet',
      api_key: 'sk-***wXyZ',
    })
    const { isDirty } = useDirty(form)
    expect(isDirty.value).toBe(false)
    form.value.model = 'claude-3-haiku'
    expect(isDirty.value).toBe(true)
  })

  it('returns to clean after snapshot is re-captured on save', () => {
    const form = ref({ provider: 'openai', model: 'gpt-4o' })
    const { isDirty, snapshot } = useDirty(form)
    form.value.model = 'gpt-4o-mini'
    expect(isDirty.value).toBe(true)
    snapshot()
    expect(isDirty.value).toBe(false)
  })
})
