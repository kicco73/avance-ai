import { defineAsyncComponent } from 'vue'

const HumanOperatorChatView = defineAsyncComponent(() => import('./components/HumanOperatorChatView.vue'))

export const key = 'webchat'

export const pushedViews = [{ view: 'operatorChat', component: HumanOperatorChatView }]

export const chatChannel = key
