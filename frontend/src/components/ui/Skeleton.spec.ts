import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import Skeleton from './Skeleton.vue'

describe('Skeleton', () => {
  it('exposes a status role for assistive tech', () => {
    const wrapper = mount(Skeleton)
    expect(wrapper.attributes('role')).toBe('status')
    expect(wrapper.attributes('aria-label')).toBe('loading')
  })

  it('applies width and height via inline style', () => {
    const wrapper = mount(Skeleton, { props: { width: '80px', height: '20px' } })
    expect(wrapper.attributes('style')).toContain('width: 80px')
    expect(wrapper.attributes('style')).toContain('height: 20px')
  })
})
