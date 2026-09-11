import { describe, expect, it } from 'vitest'

import { configured, stateListener } from '../availability.js'
import { key, chatInputControls, stateListeners } from '../index.js'

describe('the listen skill as the platform sees it', () => {
  it('is named after its own directory and contributes one chat control', () => {
    expect(key).toBe('listen')
    expect(chatInputControls.map((control) => control.id)).toEqual(['listen-mic'])
    expect(stateListeners).toEqual([stateListener])
  })

  it('reads its own flag out of the boot state, and assumes yes when the backend says nothing', () => {
    stateListener.stateReceived({ listen_enabled: false })
    expect(configured.value).toBe(false)

    stateListener.stateReceived({})
    expect(configured.value).toBe(true)
  })
})
