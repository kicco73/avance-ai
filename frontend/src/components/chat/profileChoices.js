import { markRaw, ref } from 'vue'

import { projectFileContentUrl } from '../../api.js'
import { resolveApiUrl } from '../../api/core.js'

const ABSOLUTE_URL_PATTERN = /^[a-z][a-z0-9+.-]*:|^\//i

export class ProjectMedia {
  constructor(projectId, sessionId) {
    this.projectId = projectId
    this.sessionId = sessionId
  }

  pictureUrl(url) {
    const trimmed = (url || '').trim()
    if (!trimmed || ABSOLUTE_URL_PATTERN.test(trimmed)) return resolveApiUrl(trimmed)
    return projectFileContentUrl(this.projectId, `media/${trimmed}`, this.sessionId)
  }
}

export class ProfileChoice {
  constructor(key, heading, media) {
    this.key = key
    this.heading = heading || ''
    this.media = media
    this.profiles = []
  }

  add(button) {
    this.profiles.push({ name: button.name, ...button.profile, picture_url: this.media.pictureUrl(button.profile.picture_url) })
  }

  get signature() {
    return [this.key, ...this.profiles.map((profile) => `${profile.name}=${profile.title}`)].join(',')
  }
}

export class ProfileSelection {
  constructor(choice, chat) {
    this.choice = choice
    this._chat = chat
    this._open = ref(true)
    markRaw(this)
  }

  get heading() {
    return this.choice.heading
  }

  get profiles() {
    return this.choice.profiles
  }

  get busy() {
    return this._chat.actionLoading.value
  }

  get open() {
    return this._open.value
  }

  select(name) {
    this._chat.handleAction(name)
  }

  withdraw() {
    this._open.value = false
  }
}

export function splitProfileChoices(buttons, media) {
  const plain = []
  const byKey = new Map()
  for (const button of buttons) {
    if (!button.profile) {
      plain.push(button)
      continue
    }
    const key = button.name.slice(0, button.name.lastIndexOf(':'))
    if (!byKey.has(key)) byKey.set(key, new ProfileChoice(key, button.ui_description, media))
    byKey.get(key).add(button)
  }
  return { plain, choices: [...byKey.values()] }
}
