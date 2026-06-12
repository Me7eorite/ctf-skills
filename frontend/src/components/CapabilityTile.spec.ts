import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CapabilityTile from './CapabilityTile.vue'

const stubs = {
  'router-link': {
    template: '<a><slot /></a>',
    props: ['to'],
  },
}

describe('CapabilityTile', () => {
  it('renders name, description, and "可用" badge for enabled status', () => {
    const wrapper = mount(CapabilityTile, {
      global: { stubs },
      props: {
        capability: {
          id: 'challenge-generator',
          name: '题目生成器',
          status: 'enabled',
          description: '为 web/pwn/re 三类题型批量生成题面',
          icon: 'Boxes',
          route: '/generate/new',
        },
      },
    })
    expect(wrapper.text()).toContain('题目生成器')
    expect(wrapper.text()).toContain('为 web/pwn/re 三类题型批量生成题面')
    expect(wrapper.text()).toContain('可用')
  })

  it('renders "coming soon" badge for coming_soon status', () => {
    const wrapper = mount(CapabilityTile, {
      global: { stubs },
      props: {
        capability: {
          id: 'scenario-builder',
          name: '情景生成',
          status: 'coming_soon',
          description: '装配多题情景包',
          icon: 'GitBranch',
          route: '/scenario',
        },
      },
    })
    expect(wrapper.text()).toContain('coming soon')
  })
})
