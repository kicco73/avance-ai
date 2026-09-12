import { busChannel } from './busChannel.js'

// One exchange, watched rather than awaited.
//
// Nobody here waits for anything: the person says something, and what the
// system publishes about it is observed as it arrives — the pieces of a
// message being written, the message itself, what went wrong. This object
// owns the one bubble that is being written into, and stops watching the
// moment that bubble has its message (or an error took its place).
//
// The session is the correlation: a session has at most one message being
// written at a time, so a frame for this session is this exchange's.

// A bubble that has been revealed but has shown nothing for this long
// gives up its typing dots rather than sitting there forever (e.g. an
// operator who started typing and walked away). What is being written
// keeps being written regardless — this only governs the dots.
const AWAITING_REPLY_TIMEOUT_MS = 15000

const WATCHED = ['output.text_stream', 'output.tool', 'output.text', 'output.error']

export class ChatExchange {
  // `bubble` is what to do to the one message being written: reveal,
  // append, finish, fail — the store's own, so this file knows nothing
  // about how a chat is rendered.
  constructor({ sessionId, bubble }) {
    this._sessionId = sessionId
    this._bubble = bubble
    this._unsubscribes = []
    this._awaitingTimer = null
    this.hadToolCall = false
    this.hasChunk = false
  }

  watch() {
    for (const type of WATCHED) {
      this._unsubscribes.push(busChannel.subscribe(type, (frame) => this._take(type, frame)))
    }
    return this
  }

  stop() {
    for (const unsubscribe of this._unsubscribes) unsubscribe()
    this._unsubscribes = []
    this._clearAwaitingTimer()
  }

  _take(type, frame) {
    if (frame.session_id !== this._sessionId) return
    if (type === 'output.text_stream') this._streamed(frame.text)
    else if (type === 'output.tool') this._tool(frame)
    else if (type === 'output.text') this._said(frame)
    else if (type === 'output.error') this._failed(frame)
  }

  // An empty piece means the writing has started and nothing is readable
  // yet: that is when the bubble appears, and never any sooner.
  _streamed(text) {
    if (text === '') {
      this._bubble.writing()
      this._clearAwaitingTimer()
      this._awaitingTimer = setTimeout(() => this._bubble.stopWaiting(), AWAITING_REPLY_TIMEOUT_MS)
      return
    }
    this.hasChunk = true
    this._clearAwaitingTimer()
    this._bubble.append(text)
  }

  _tool(frame) {
    const text = frame.phase === 'start' ? frame.status_text || '' : ''
    if (text) this.hadToolCall = true
    this._bubble.status(text)
  }

  _said(frame) {
    this.stop()
    this._bubble.said({
      id: frame.assistant_message_id, content: frame.text, audio_text: frame.audio_text
    })
  }

  _failed(frame) {
    this.stop()
    this._bubble.failed(frame)
  }

  _clearAwaitingTimer() {
    clearTimeout(this._awaitingTimer)
    this._awaitingTimer = null
  }
}
