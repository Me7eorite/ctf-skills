import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import Command, { type CommandEntry } from './Command.vue'

const entries: CommandEntry[] = [
  { id: 'a', label: 'Overview', group: 'Pages' },
  { id: 'b', label: 'New Run', group: 'Generate' },
  { id: 'c', label: 'Settings · LLM Provider', group: 'Settings' },
]

const teleportStub = {
  global: { stubs: { teleport: true, transition: false } },
}

describe('Command', () => {
  it('filters entries by case-insensitive substring', async () => {
    const wrapper = mount(Command, { ...teleportStub, props: { open: true, entries } })
    const input = wrapper.find('input')
    await input.setValue('settings')
    const options = wrapper.findAll('[role="option"]')
    expect(options).toHaveLength(1)
    expect(options[0].text()).toContain('Settings · LLM Provider')
  })

  it('renders all entries when query is empty', () => {
    const wrapper = mount(Command, { ...teleportStub, props: { open: true, entries } })
    expect(wrapper.findAll('[role="option"]')).toHaveLength(entries.length)
  })
})
