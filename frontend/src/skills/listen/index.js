import { defineAsyncComponent } from 'vue'
import MicButton from './components/MicButton.vue'
import { stateListener } from './availability.js'

const ServicesTab = defineAsyncComponent(() => import('./components/ServicesTab.vue'))

export const key = 'listen'

export const chatInputControls = [{ id: 'listen-mic', component: MicButton }]

export const stateListeners = [stateListener]

export const servicesTabs = [{ id: 'listen', label: 'Listen', component: ServicesTab }]
