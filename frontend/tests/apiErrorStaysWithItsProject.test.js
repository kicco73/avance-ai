import { describe, it, expect, beforeEach, vi } from 'vitest'
import { apiFetch } from '../src/api/core.js'
import { enterScreen, errorMessage } from '../src/errorStore.js'

const BROKEN = "Project 'derivador_edu', stored revision 18: index.yml no longer builds — Env key 'new_env_key': 'type' is required."

function refusal(message) {
  return {
    ok: false,
    status: 409,
    json: async () => ({ error: { message, detail: null, code: 'project_broken', fields: { file: 'index.yml', line: 109 } } })
  }
}

async function ask(url) {
  await apiFetch(url).catch(() => null)
}

describe("a failure about one project, while another one is open", () => {
  beforeEach(() => {
    global.fetch = vi.fn(async () => refusal(BROKEN))
  })

  it('is never shown in the editor of a different project', async () => {
    enterScreen('edit', 'andre_the_game')

    await ask('/api/skills/platform/projects/derivador_edu/project')

    expect(errorMessage.value).toBe('')
  })

  it("is shown in the editor of the project it is about", async () => {
    enterScreen('edit', 'derivador_edu')

    await ask('/api/skills/platform/projects/derivador_edu/project')

    expect(errorMessage.value).toBe(BROKEN)
  })

  it('is shown on a screen that is about no project in particular', async () => {
    enterScreen('home')

    await ask('/api/skills/platform/projects/derivador_edu/project')

    expect(errorMessage.value).toBe(BROKEN)
  })

  it('still reaches the editor when the request names no project', async () => {
    global.fetch = vi.fn(async () => refusal('The backend is unhappy.'))
    enterScreen('edit', 'andre_the_game')

    await ask('/api/core/projects')

    expect(errorMessage.value).toBe('The backend is unhappy.')
  })

  it('is kept off the editor when the backend cannot be reached at all', async () => {
    global.fetch = vi.fn(async () => { throw new Error('offline') })
    enterScreen('edit', 'andre_the_game')

    await ask('/api/skills/platform/projects/derivador_edu/files')

    expect(errorMessage.value).toBe('')
  })
})
