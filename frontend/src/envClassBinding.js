import { computed, ref } from 'vue'
import { busChannel } from './busChannel.js'

function classToken(value) {
  return String(value).replace(/[^A-Za-z0-9_-]/g, '_')
}

export class EnvClassBinding {
  constructor(sessionId) {
    this._sessionId = sessionId
    this._bound = ref({ sessionId: null, values: {} })
    this.classes = computed(() => Object.entries(this._values())
      .filter(([, value]) => value != null && value !== '')
      .map(([key, value]) => `env-${classToken(key)}-${classToken(value)}`))
    busChannel.subscribe('env.bindings', (frame) => {
      this._bound.value = { sessionId: frame.session_id, values: frame.values ?? {} }
    })
  }

  _values() {
    return this._bound.value.sessionId === this._sessionId.value ? this._bound.value.values : {}
  }
}
