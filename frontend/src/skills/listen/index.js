import MicButton from './components/MicButton.vue'
import ServicesTab from './components/ServicesTab.vue'
import { stateListener } from './availability.js'

export const key = 'listen'

export const chatInputControls = [{ id: 'listen-mic', component: MicButton }]

export const stateListeners = [stateListener]

export const servicesTabs = [{ id: 'listen', label: 'Listen', component: ServicesTab }]
