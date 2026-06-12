import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import EmptyState from './EmptyState.vue'

describe('EmptyState', () => {
  it('renders title, description, and CTA button', () => {
    const wrapper = mount(EmptyState, {
      props: {
        title: '还没有 Run',
        description: '开始第一次 Run 来生成题目',
        ctaLabel: '开始 Run',
      },
    })
    expect(wrapper.text()).toContain('还没有 Run')
    expect(wrapper.text()).toContain('开始第一次 Run 来生成题目')
    expect(wrapper.find('button').text()).toBe('开始 Run')
  })

  it('emits cta when CTA is clicked without target route', async () => {
    const wrapper = mount(EmptyState, {
      props: { title: 't', ctaLabel: 'go' },
    })
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('cta')).toHaveLength(1)
  })
})
