import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../playback.js', () => ({ playMessageAudio: vi.fn() }))

import { playMessageAudio } from '../playback.js'
import { audioEnabled } from '../../../chatPreferences.js'
import { narrator } from '../narrator.js'
import { configured, stateListener } from '../availability.js'
import { key, chatInputControls, messageListeners, stateListeners } from '../index.js'

describe('the talk skill as the platform sees it', () => {
  it('is named after its own directory and contributes its own controls', () => {
    expect(key).toBe('talk')
    expect(chatInputControls.map((control) => control.id)).toEqual(['talk-audio', 'talk-spoken-text'])
    expect(messageListeners).toEqual([narrator])
    expect(stateListeners).toEqual([stateListener])
  })

  it('reads its own flag out of the boot state, and assumes yes when the backend says nothing', () => {
    stateListener.stateReceived({ talk_enabled: false })
    expect(configured.value).toBe(false)

    stateListener.stateReceived({})
    expect(configured.value).toBe(true)
  })
})

describe('narrating a reply', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    audioEnabled.value = false
  })

  it('stays silent while the listener has audio off', () => {
    narrator.messageArrived(42)
    expect(playMessageAudio).not.toHaveBeenCalled()
  })

  it('plays the arriving message once audio is on', () => {
    audioEnabled.value = true

    narrator.messageArrived(42)

    expect(playMessageAudio).toHaveBeenCalledWith(expect.stringContaining('/skills/talk/messages/42/audio'))
  })

  it('narrates the latest assistant message, never a user one', () => {
    narrator.narrateLatest([
      { role: 'assistant', messageId: 1 },
      { role: 'assistant', messageId: 2 },
      { role: 'user', messageId: 3 },
    ])

    expect(playMessageAudio).toHaveBeenCalledWith(expect.stringContaining('/messages/2/audio'))
  })

  it('does nothing when there is no assistant message to narrate', () => {
    narrator.narrateLatest([{ role: 'user', messageId: 3 }])
    expect(playMessageAudio).not.toHaveBeenCalled()
  })
})
