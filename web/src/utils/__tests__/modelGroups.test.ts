import { describe, it, expect } from 'vitest'
import { groupAvailableModels } from '../modelGroups'

describe('groupAvailableModels', () => {
  it('groups cloud models under "Cloud" and locals under "provider · host"', () => {
    const groups = groupAvailableModels([
      { id: 'anthropic/claude-haiku-4.5', name: 'Claude Haiku 4.5', provider: 'Anthropic' },
      { id: 'zeta', name: 'Zeta', provider: 'oMLX', host: 'studio.riechers.co:8000', backend: 'studio.riechers.co:8000' },
      { id: 'alpha', name: 'Alpha', provider: 'oMLX', host: 'studio.riechers.co:8000', backend: 'studio.riechers.co:8000' },
    ])
    expect(groups.map((g) => g.label)).toEqual(['Cloud', 'oMLX · studio.riechers.co:8000'])
    expect(groups[0].models.map((m) => m.id)).toEqual(['anthropic/claude-haiku-4.5'])
    // sorted by name within a group
    expect(groups[1].models.map((m) => m.id)).toEqual(['alpha', 'zeta'])
  })

  it('omits the Cloud group when there are no cloud models', () => {
    const groups = groupAvailableModels([{ id: 'x', name: 'X', provider: 'oMLX', host: 'h:1' }])
    expect(groups.map((g) => g.label)).toEqual(['oMLX · h:1'])
  })

  it('separates two local servers into their own groups', () => {
    const groups = groupAvailableModels([
      { id: 'a', name: 'A', provider: 'oMLX', host: 'studio:8000' },
      { id: 'b', name: 'B', provider: 'vLLM', host: 'gpu-box:8000' },
    ])
    expect(groups.map((g) => g.label).sort()).toEqual(['oMLX · studio:8000', 'vLLM · gpu-box:8000'])
  })
})
