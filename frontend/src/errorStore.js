import { computed, shallowRef } from 'vue'

class Screen {
  constructor(view) {
    this.view = view
  }

  get key() {
    return this.view
  }

  is(other) {
    return other.key === this.key
  }

  shows() {
    return true
  }
}

class ProjectScreen extends Screen {
  constructor(view, projectId) {
    super(view)
    this.projectId = projectId
  }

  get key() {
    return `${this.view}:${this.projectId}`
  }

  shows(projectId) {
    return projectId === this.projectId
  }
}

class Notice {
  constructor(message, detail, severity, screen) {
    this.message = message
    this.detail = detail
    this.severity = severity
    this.screen = screen
  }

  visibleOn(screen) {
    return this.screen.is(screen)
  }
}

class ProjectNotice extends Notice {
  constructor(message, detail, severity, screen, projectId) {
    super(message, detail, severity, screen)
    this.projectId = projectId
  }

  visibleOn(screen) {
    return super.visibleOn(screen) && screen.shows(this.projectId)
  }
}

class NoNotice {
  constructor() {
    this.message = ''
    this.detail = ''
    this.severity = 'error'
  }

  visibleOn() {
    return false
  }
}

const NOTHING = new NoNotice()

export const currentScreen = shallowRef(new Screen(''))

const raised = shallowRef(NOTHING)
const displayed = computed(() => (raised.value.visibleOn(currentScreen.value) ? raised.value : NOTHING))

export const errorMessage = computed(() => displayed.value.message)
export const errorDetail = computed(() => displayed.value.detail)
export const errorSeverity = computed(() => displayed.value.severity)

function raise(text, detail, severity, screen, aboutProject) {
  raised.value = aboutProject
    ? new ProjectNotice(text, detail, severity, screen, aboutProject)
    : new Notice(text, detail, severity, screen)
}

export function setApiError(text, moreDetail = '', screen = currentScreen.value, aboutProject = null) {
  raise(text, moreDetail || '', 'error', screen, aboutProject)
}

export function setApiWarning(text, moreDetail = '', screen = currentScreen.value, aboutProject = null) {
  raise(text, moreDetail || '', 'warning', screen, aboutProject)
}

export function clearApiError() {
  raised.value = NOTHING
}

export function enterScreen(view, projectId = '') {
  currentScreen.value = projectId ? new ProjectScreen(view, projectId) : new Screen(view)
  clearApiError()
}
