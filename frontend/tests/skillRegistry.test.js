import { beforeEach, describe, expect, it, vi } from 'vitest'

// The real roster is a ref, and the registry's computeds depend on it —
// a plain object here would never invalidate them.
const installed = vi.hoisted(() => ({ keys: null }))
vi.mock('../src/skillRoster.js', async () => {
  const { ref } = await import('vue')
  installed.keys = ref(null)
  return {
    isSkillInstalled: (key) => installed.keys.value === null || installed.keys.value.includes(key),
  }
})

import { compiledSkillKeys, chatInputControls, servicesTabs, channelLabels } from '../src/skills/registry.js'

describe('the registry as the app sees it', () => {
  beforeEach(() => { installed.keys.value = null })

  it('finds whatever skill directories were compiled in, without a list anywhere', () => {
    expect(compiledSkillKeys.length).toBeGreaterThan(0)
    expect([...compiledSkillKeys]).toEqual([...compiledSkillKeys].sort())
  })

  it('offers nothing from a skill this backend does not have', () => {
    const withEverything = servicesTabs.value.length

    installed.keys.value = []

    expect(servicesTabs.value).toEqual([])
    expect(chatInputControls.value).toEqual([])
    expect(channelLabels.value).toEqual({})
    expect(withEverything).toBeGreaterThan(0)
  })

  it('keeps a skill whose backend reports it, and drops the others', () => {
    installed.keys.value = ['build']

    expect(servicesTabs.value.map((tab) => tab.id)).toEqual(['build'])
  })

  it('shows everything while the roster is still unknown, so nothing flickers away at boot', () => {
    installed.keys.value = null

    expect(servicesTabs.value.length).toBeGreaterThan(1)
  })
})
