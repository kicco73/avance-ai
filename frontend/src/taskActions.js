import { defineAsyncComponent } from 'vue'
import { celebrate } from './confetti.js'
import { notify } from './toastStore.js'
import { infoDialog, customDialog } from './dialogStore.js'
import { playBackgroundAudio } from './backgroundAudioStore.js'
import { mediaKindFromUrl } from './mediaKind.js'

const MediaDialog = defineAsyncComponent(() => import('./components/MediaDialog.vue'))

function show(body_md) {
  infoDialog({ body: body_md, markdown: true })
}

function show_media(url) {
  if (mediaKindFromUrl(url) === 'audio') {
    playBackgroundAudio(url)
    return
  }
  customDialog({ component: MediaDialog, props: { url }, wide: true })
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
