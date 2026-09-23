import { computed, ref } from 'vue'
import { busChannel } from './busChannel.js'

function classToken(value) {
  return String(value).replace(/[^A-Za-z0-9_-]/g, '_')
}

export class EnvClassBinding {
  constructor(sessionId) {
    this._sessionId = sessionId
    this._values = ref({})
    this._unsubscribe = null
    this.classes = computed(() => Object.entries(this._values.value)
      .filter(([, value]) => value != null && value !== '')
      .map(([key, value]) => `env-${classToken(key)}-${classToken(value)}`))
  }

  bind(key, value) {
    this._values.value = { ...this._values.value, [key]: value }
    this._unsubscribe ??= busChannel.subscribe('env.changed', (frame) => this._changed(frame))
  }

  unbindAll() {
    this._unsubscribe?.()
    this._unsubscribe = null
    this._values.value = {}
  }

  _changed(frame) {
    if (frame.session_id !== this._sessionId.value) return
    if (!(frame.key in this._values.value)) return
    this._values.value = { ...this._values.value, [frame.key]: frame.value }
  }
}
