import { busChannel } from './busChannel.js'

const AWAITING_REPLY_TIMEOUT_MS = 15000

const WATCHED = ['output.text_stream', 'output.tool', 'output.speech', 'output.text', 'output.error']

export class ChatExchange {
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

  receive(frame) {
    this._take(frame.type, frame)
  }

  stop() {
    for (const unsubscribe of this._unsubscribes) unsubscribe()
    this._unsubscribes = []
    this._clearAwaitingTimer()
  }

  _take(type, frame) {
    if (frame.session_id !== this._sessionId) return
    if (type === 'output.text_stream') this._streamed(frame.text)
    else if (type === 'output.speech') this._bubble.spoken(frame.text)
    else if (type === 'output.tool') this._tool(frame)
    else if (type === 'output.text') this._said(frame)
    else if (type === 'output.error') this._failed(frame)
  }

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
    this._bubble.said({ id: frame.assistant_message_id, content: frame.text, timestamp: frame.timestamp })
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
