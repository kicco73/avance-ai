import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, ref } from 'vue'

import SelectProfileDialog from '../src/components/chat/SelectProfileDialog.vue'
import DialogHost from '../src/components/DialogHost.vue'
import { activeDialog, blockingDialog, resolveActiveDialog } from '../src/dialogStore.js'
import { ProfileSelection, ProjectMedia, splitProfileChoices } from '../src/components/chat/profileChoices.js'
import { projectFileContentUrl } from '../src/api.js'
import { resolveApiUrl } from '../src/api/core.js'

const ADA = { title: 'Ada', picture_url: 'ada.png', description: 'The analyst.', key: 'ada' }
const GRACE = { title: 'Grace', picture_url: 'https://example.com/grace.png', description: 'The admiral.', key: 'grace' }
const MEDIA = new ProjectMedia('heroes', 7)

const BUTTONS = [
  { name: 'manual', ui_button: 'Manual' },
  { name: 'choice:hero:0', ui_button: 'Ada', ui_description: 'Pick your hero', profile: ADA },
  { name: 'choice:hero:1', ui_button: 'Grace', ui_description: 'Pick your hero', profile: GRACE },
]

describe('splitting a button row', () => {
  it('keeps plain buttons as they are and groups profile buttons under their choice key', () => {
    const { plain, choices } = splitProfileChoices(BUTTONS, MEDIA)

    expect(plain.map((b) => b.name)).toEqual(['manual'])
    expect(choices).toHaveLength(1)
    expect(choices[0].heading).toBe('Pick your hero')
    expect(choices[0].profiles.map((p) => [p.name, p.title])).toEqual([['choice:hero:0', 'Ada'], ['choice:hero:1', 'Grace']])
  })

  it('signs the same offer the same way, so a re-sent button row does not reopen the dialog', () => {
    const [choice] = splitProfileChoices(BUTTONS, MEDIA).choices
    const [again] = splitProfileChoices(BUTTONS, new ProjectMedia('heroes', 8)).choices
    const [other] = splitProfileChoices(BUTTONS.slice(0, 2), MEDIA).choices

    expect(again.signature).toBe(choice.signature)
    expect(other.signature).not.toBe(choice.signature)
  })

  it('finds no profile choice in a row of plain string options', () => {
    expect(splitProfileChoices([{ name: 'choice:slot:0', ui_button: 'morning' }], MEDIA).choices).toEqual([])
  })

  it('serves a bare picture name from the project media, like a skin asset, and leaves an absolute URL alone', () => {
    const [ada, grace] = splitProfileChoices(BUTTONS, MEDIA).choices[0].profiles

    expect(ada.picture_url).toBe(projectFileContentUrl('heroes', 'media/ada.png', 7))
    expect(grace.picture_url).toBe('https://example.com/grace.png')
  })

  it('sends the path media.<name>.url() writes to the API origin, as show_media does', () => {
    const url = '/api/core/projects/heroes/files/media/ada.png/content'
    const buttons = [{ name: 'choice:hero:0', ui_button: 'Ada', profile: { ...ADA, picture_url: url } }]

    expect(splitProfileChoices(buttons, MEDIA).choices[0].profiles[0].picture_url).toBe(resolveApiUrl(url))
  })
})

function fakeChat() {
  return { actionLoading: ref(false), handleAction: vi.fn() }
}

function selectionFor(buttons, chat) {
  return new ProfileSelection(splitProfileChoices(buttons, MEDIA).choices[0], chat)
}

describe('the select profile dialog', () => {
  let container
  let app
  let closed
  let chat
  let selection

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    closed = vi.fn()
    Element.prototype.animate = () => ({ finished: Promise.resolve() })
    chat = fakeChat()
    selection = selectionFor(BUTTONS, chat)
    app = createApp(SelectProfileDialog, { selection })
    app.provide('closeDialog', closed)
    app.mount(container)
  })

  afterEach(() => {
    app.unmount()
    container.remove()
  })

  const title = () => container.querySelector('.profile-card.is-current .profile-title').textContent
  const click = (selector) => container.querySelector(selector).click()

  async function settle() {
    for (let i = 0; i < 4; i += 1) await nextTick()
  }

  it('draws no avatar for a profile that carries no picture', () => {
    const { choices } = splitProfileChoices(
      [{ name: 'choice:hero:0', ui_button: 'Ada', profile: { title: 'Ada', description: 'The analyst.', key: 'ada' } }],
      MEDIA,
    )
    const plain = document.createElement('div')
    document.body.appendChild(plain)
    const pictureless = createApp(SelectProfileDialog, {
      selection: new ProfileSelection(choices[0], fakeChat())
    })
    pictureless.provide('closeDialog', closed)
    pictureless.mount(plain)

    expect(plain.querySelector('.profile-avatar')).toBe(null)
    expect(plain.querySelector('.profile-title').textContent).toBe('Ada')

    pictureless.unmount()
    plain.remove()
  })

  it('shows the first profile with its avatar, title and description', () => {
    expect(title()).toBe('Ada')
    expect(container.querySelector('.profile-card.is-current .profile-avatar').getAttribute('src')).toBe(projectFileContentUrl('heroes', 'media/ada.png', 7))
    expect(container.querySelector('.profile-card.is-current .profile-description').textContent).toBe('The analyst.')
    expect(container.querySelector('.profile-heading').textContent).toBe('Pick your hero')
  })

  it('moves to the next profile on the right arrow and wraps around on the left one', async () => {
    click('[aria-label="Next"]')
    await settle()
    expect(title()).toBe('Grace')

    click('[aria-label="Next"]')
    await settle()
    expect(title()).toBe('Ada')

    click('[aria-label="Previous"]')
    await settle()
    expect(title()).toBe('Grace')
  })

  it('sends the shown profile when its key button is pressed, and stays open until the turn says how it went', async () => {
    click('[aria-label="Next"]')
    await settle()
    expect(container.querySelector('.profile-select').textContent).toBe('grace')

    click('.profile-select')

    expect(chat.handleAction).toHaveBeenCalledWith('choice:hero:1')
    expect(closed).not.toHaveBeenCalled()
  })

  const disabled = (selector) => container.querySelector(selector).disabled

  it('takes no second answer while the turn it started is still running', async () => {
    click('.profile-select')
    chat.actionLoading.value = true
    await settle()

    expect([disabled('.profile-select'), disabled('[aria-label="Next"]'), disabled('[aria-label="Previous"]')])
      .toEqual([true, true, true])

    click('[aria-label="Next"]')
    await settle()
    expect(title()).toBe('Ada')
  })

  it('asks again when the turn failed, since a failed press left the same choice standing', async () => {
    click('.profile-select')
    chat.actionLoading.value = true
    await settle()
    chat.actionLoading.value = false
    await settle()

    expect(disabled('.profile-select')).toBe(false)
    expect(closed).not.toHaveBeenCalled()

    click('.profile-select')
    expect(chat.handleAction).toHaveBeenCalledTimes(2)
  })

  it('closes when the choice it was asking is withdrawn, the turn having moved on', async () => {
    selection.withdraw()
    await settle()

    expect(closed).toHaveBeenCalledWith(null)
  })
})

describe('the select profile dialog as the dialog host hands it over', () => {
  let container
  let app
  let chat
  let selection

  async function shown() {
    await nextTick()
    await new Promise((resolve) => requestAnimationFrame(resolve))
    await nextTick()
  }

  function ask(profileChat = chat) {
    const asked = selectionFor(BUTTONS, profileChat)
    blockingDialog({ component: SelectProfileDialog, props: { selection: asked } })
    return asked
  }

  beforeEach(async () => {
    container = document.createElement('div')
    document.body.appendChild(container)
    Element.prototype.animate = () => ({ finished: Promise.resolve() })
    app = createApp(DialogHost)
    app.mount(container)
    chat = fakeChat()
    selection = ask()
    await shown()
  })

  afterEach(() => {
    while (activeDialog.value) resolveActiveDialog(null)
    app.unmount()
    container.remove()
  })

  it('still reads the turn it is waiting on through the host, whose queue would otherwise wrap it', async () => {
    expect(container.querySelector('.profile-select').disabled).toBe(false)

    chat.actionLoading.value = true
    await nextTick()

    expect(container.querySelector('.profile-select').disabled).toBe(true)

    chat.actionLoading.value = false
    await nextTick()

    expect(container.querySelector('.profile-select').disabled).toBe(false)
  })

  it('opens the next asking on its first profile, however far the last one was browsed', async () => {
    container.querySelector('[aria-label="Next"]').click()
    await shown()
    expect(container.querySelector('.profile-card.is-current .profile-title').textContent).toBe('Grace')

    ask()
    selection.withdraw()
    await new Promise((resolve) => setTimeout(resolve, 250))
    await shown()

    expect(container.querySelector('.profile-card.is-current .profile-title').textContent).toBe('Ada')
  })
})
