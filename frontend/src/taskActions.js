import { defineAsyncComponent } from 'vue'
import { celebrate } from './confetti.js'
import { notify } from './toastStore.js'
import { infoDialog, customDialog } from './dialogStore.js'
import { mediaKindFromUrl } from './mediaKind.js'
import { resolveApiUrl } from './api/core.js'

const MediaDialog = defineAsyncComponent(() => import('./components/MediaDialog.vue'))

function show(body_md, scopeEl) {
  infoDialog({ body: body_md, markdown: true, scopeEl })
}

function show_media(url, { playBackgroundAudio, scopeEl }) {
  const resolvedUrl = resolveApiUrl(url)
  if (mediaKindFromUrl(resolvedUrl) === 'audio') {
    playBackgroundAudio(resolvedUrl)
    return
  }
  customDialog({ component: MediaDialog, props: { url: resolvedUrl }, wide: true, scopeEl })
}

export function runTaskScript(script, { playBackgroundAudio, scopeEl = null } = {}) {
  if (!script) return
  try {
    const taskLocals = {
      celebrate: (duration) => celebrate(duration, scopeEl),
      notify: (title, body) => notify(title, body, scopeEl),
      show: (body_md) => show(body_md, scopeEl),
      show_media: (url) => show_media(url, { playBackgroundAudio, scopeEl }),
    }
    const names = Object.keys(taskLocals)
    const values = names.map((name) => taskLocals[name])
    const run = new Function(...names, script)
    run(...values)
  } catch (err) {
    console.error('task script failed:', script, err)
  }
}
