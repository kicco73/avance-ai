import HumanOperatorChatView from './components/HumanOperatorChatView.vue'
import { getCurrentSession, postCreateSession, getOperatorState, getMessages } from './api.js'

export const key = 'webchat'

// The one contribution: how the app's live chat reaches a backend that
// knows the chat window is the one speaking. A build without this
// directory contributes none, and the core's null object stands in —
// the live chat then opens no session at all, which is what a product
// delivered for another channel looks like (see ../../liveChatChannel.js).
export const liveChatChannels = [{ getCurrentSession, createSession: postCreateSession, getOperatorState, getMessages }]

export const pushedViews = [{ view: 'operatorChat', component: HumanOperatorChatView }]
