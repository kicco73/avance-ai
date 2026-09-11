import { describe, expect, it } from 'vitest'
import { key, servicesTabs, profileFields, shareChannels, channelLabels } from '../index.js'

describe('the whatsapp skill as the platform sees it', () => {
  it('is named after its own directory', () => {
    expect(key).toBe('whatsapp')
  })

  it('contributes one tab, one profile field and one way of sharing an invite', () => {
    expect(servicesTabs.map((tab) => tab.id)).toEqual(['whatsapp'])
    expect(profileFields.map((field) => field.id)).toEqual(['whatsapp-phone'])
    expect(shareChannels.map((channel) => channel.id)).toEqual(['whatsapp'])
  })

  it('names the invite field its link arrives in, rather than fetching it itself', () => {
    expect(shareChannels[0].inviteField).toBe('whatsapp_url')
    expect(shareChannels[0].hint).toMatch(/WhatsApp/)
  })

  it('labels its own channel in a session list', () => {
    expect(channelLabels).toEqual({ 'whatsapp-chat': 'WhatsApp' })
  })
})
