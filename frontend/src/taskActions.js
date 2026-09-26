import { celebrate } from './confetti.js'
import { notifyInChat } from './components/chat/chatNotifications.js'
import { infoDialog } from './dialogStore.js'
import { mediaKindFromUrl } from './mediaKind.js'
import { resolveApiUrl } from './api/core.js'
import { openMediaDialog } from './openMediaDialog.js'

function show(body_md) {
  infoDialog({ body: body_md, markdown: true })
}

function show_media(url, { playBackgroundAudio }) {
  const resolvedUrl = resolveApiUrl(url)
  if (mediaKindFromUrl(resolvedUrl) === 'audio') {
    playBackgroundAudio(resolvedUrl)
    return
  }
  openMediaDialog(resolvedUrl)
}

export function runTaskScript(script, { playBackgroundAudio, clearTranscript } = {}) {
  if (!script) return
  try {
    const taskLocals = {
      celebrate,
      notify: (title, body, iconUrl) => notifyInChat(title, body, iconUrl ? resolveApiUrl(iconUrl) : null),
      show,
      show_media: (url) => show_media(url, { playBackgroundAudio }),
      clear: () => clearTranscript?.(),
    }
    const names = Object.keys(taskLocals)
    const values = names.map((name) => taskLocals[name])
    const run = new Function(...names, script)
    run(...values)
  } catch (err) {
    console.error('task script failed:', script, err)
  }
}
