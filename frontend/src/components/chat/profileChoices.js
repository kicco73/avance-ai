import { projectFileContentUrl } from '../../api.js'
import { resolveApiUrl } from '../../api/core.js'

const REOPEN_PREFIX = 'profiles:'
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

  get reopenName() {
    return REOPEN_PREFIX + this.key
  }

  get reopenButton() {
    return { name: this.reopenName, ui_button: this.heading || 'Select profile', ui_label: this.heading || 'Select profile' }
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
