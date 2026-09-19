import { customDialog } from '../../../../dialogStore.js'
import { getProjectFile, getWebSearchCache } from '../../api.js'
import { sourceDriverOf, WEBSEARCH_DRIVER } from '../../sourceDrivers.js'
import SourceCsvDialog from './SourceCsvDialog.vue'

const EMBEDDED_DRIVER = 'avance'

function targetOf(source) {
  const url = source?.url ?? ''
  return url.slice(url.indexOf(':') + 1)
}

class WebSearchCacheReader {
  constructor(sessionId) {
    this.sessionId = sessionId
  }

  async read() {
    return (await getWebSearchCache(this.sessionId))?.content ?? ''
  }
}

class ProjectFileReader {
  constructor(projectId, fileName) {
    this.projectId = projectId
    this.fileName = fileName
  }

  async read() {
    return (await getProjectFile(this.projectId, this.fileName))?.content ?? ''
  }
}

const webSearchInspector = {
  caption() {
    return 'What task.websearch(…) last found for this session — click to view.'
  },
  isReachable({ sessionId }) {
    return sessionId != null
  },
  open(source, { sessionId }) {
    customDialog({
      component: SourceCsvDialog,
      props: {
        title: source.ui_label || source.name,
        hint: 'What task.websearch(…) last found for this session — read-only, wiped when the session closes.',
        emptyLabel: 'Nothing has been searched yet this session.',
        reader: new WebSearchCacheReader(sessionId)
      }
    })
  }
}

const embeddedInspector = {
  caption(source) {
    return `${targetOf(source)} — click to view.`
  },
  isReachable() {
    return true
  },
  open(source, { projectId }) {
    customDialog({
      component: SourceCsvDialog,
      props: {
        title: source.ui_label || source.name,
        hint: `${targetOf(source)} — read-only here; edit it in the design view.`,
        emptyLabel: 'This file has no rows.',
        reader: new ProjectFileReader(projectId, targetOf(source))
      }
    })
  }
}

const unknownInspector = {
  caption(source) {
    return source?.url ?? ''
  },
  isReachable() {
    return false
  },
  open() {
  }
}

const INSPECTORS = {
  [WEBSEARCH_DRIVER]: webSearchInspector,
  [EMBEDDED_DRIVER]: embeddedInspector
}

export function inspectorFor(source) {
  return INSPECTORS[sourceDriverOf(source)] ?? unknownInspector
}
