import { liveChatWakeup } from './liveChatWakeup.js'
import { eventTriggerNamespace } from './triggerNamespace.js'

export const key = 'event'

export const liveChatObservers = [liveChatWakeup]

export const triggerNamespaces = [eventTriggerNamespace]
