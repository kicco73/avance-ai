import { defineAsyncComponent } from 'vue'
import { celebrate } from './confetti.js'
import { notify } from './toastStore.js'
import { infoDialog, customDialog } from './dialogStore.js'
import { playBackgroundAudio } from './backgroundAudioStore.js'
import { mediaKindFromUrl } from './mediaKind.js'
import { resolveApiUrl } from './api/core.js'

const MediaDialog = defineAsyncComponent(() => import('./components/MediaDialog.vue'))

function show(body_md) {
  infoDialog({ body: body_md, markdown: true })
}

function show_media(url) {
  const resolvedUrl = resolveApiUrl(url)
  if (mediaKindFromUrl(resolvedUrl) === 'audio') {
    playBackgroundAudio(resolvedUrl)
    return
  }
  customDialog({ component: MediaDialog, props: { url: resolvedUrl }, wide: true })
}

export const taskLocals = { celebrate, notify, show, show_media }

export function runTaskScript(script) {
  if (!script) return
  try {
    const names = Object.keys(taskLocals)
    const values = names.map((name) => taskLocals[name])
    const run = new Function(...names, script)
    run(...values)
  } catch (err) {
    console.error('task script failed:', script, err)
  }
}
