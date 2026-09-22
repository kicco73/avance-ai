import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'

import SelectProfileDialog from '../src/components/chat/SelectProfileDialog.vue'
import { ProjectMedia, splitProfileChoices } from '../src/components/chat/profileChoices.js'
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
    expect(choices[0].reopenButton.ui_button).toBe('Pick your hero')
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

describe('the select profile dialog', () => {
  let container
  let app
  let closed

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    closed = vi.fn()
    Element.prototype.animate = () => ({ finished: Promise.resolve() })
    const { choices } = splitProfileChoices(BUTTONS, MEDIA)
    app = createApp(SelectProfileDialog, { heading: choices[0].heading, profiles: choices[0].profiles })
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

  it('closes the dialog with the shown profile button name when Select is pressed', async () => {
    click('[aria-label="Next"]')
    await settle()
    click('.profile-select')

    expect(closed).toHaveBeenCalledWith('choice:hero:1')
  })
})
