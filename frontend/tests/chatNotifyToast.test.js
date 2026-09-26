import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick } from 'vue'

vi.mock('../src/confetti.js', () => ({ celebrate: vi.fn() }))

const { runTaskScript } = await import('../src/taskActions.js')
const { chatNotifications, dismissChatNotification } = await import('../src/components/chat/chatNotifications.js')
const { toasts } = await import('../src/toastStore.js')
const ChatToastStack = (await import('../src/components/chat/ChatToastStack.vue')).default

describe('chat.notify', () => {
  afterEach(() => {
    for (const notification of chatNotifications.value) dismissChatNotification(notification.id)
  })

  it('lands in the chat, with the progress toast\'s card, and never among the platform toasts', async () => {
    runTaskScript("notify('Well done', 'You finished **step 2**.')")

    expect(toasts.value).toEqual([])
    expect(chatNotifications.value.map((n) => [n.title, n.body])).toEqual([['Well done', 'You finished **step 2**.']])

    const host = document.createElement('div')
    document.body.appendChild(host)
    createApp({ setup: () => () => h(ChatToastStack, { progress: null }) }).mount(host)
    await nextTick()

    const card = host.querySelector('.chat-notification')
    expect(card.classList.contains('chat-toast-card')).toBe(true)
    expect(card.textContent).toContain('Well done')
    expect(card.querySelector('strong').textContent).toBe('step 2')

    card.click()
    await nextTick()
    expect(chatNotifications.value).toEqual([])
  })

  it('shows the icon at 48x48 in the left column, title and text to its right', async () => {
    runTaskScript("notify('Trophy', 'Well done', '/api/core/projects/p/files/media/trophy.png/content')")

    const host = document.createElement('div')
    document.body.appendChild(host)
    createApp({ setup: () => () => h(ChatToastStack, { progress: null }) }).mount(host)
    await nextTick()

    const layout = host.querySelector('.chat-notification-layout')
    const icon = layout.firstElementChild
    expect(icon.tagName).toBe('IMG')
    expect(icon.classList.contains('chat-notification-icon')).toBe(true)
    expect(icon.getAttribute('src')).toMatch(/\/core\/projects\/p\/files\/media\/trophy\.png\/content$/)
    expect(layout.querySelector('.chat-notification-text').textContent).toContain('Trophy')
  })

  it('draws no icon column when no icon is given', async () => {
    runTaskScript("notify('Plain', 'No icon')")

    const host = document.createElement('div')
    document.body.appendChild(host)
    createApp({ setup: () => () => h(ChatToastStack, { progress: null }) }).mount(host)
    await nextTick()

    expect(host.querySelector('.chat-notification-icon')).toBeNull()
  })
})
