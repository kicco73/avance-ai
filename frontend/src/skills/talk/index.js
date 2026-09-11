import AudioToggleButton from './components/AudioToggleButton.vue'
import SpokenTextButton from './components/SpokenTextButton.vue'
import ServicesTab from './components/ServicesTab.vue'
import { stateListener } from './availability.js'
import { narrator } from './narrator.js'

export const key = 'talk'

export const chatInputControls = [
  { id: 'talk-audio', component: AudioToggleButton },
  { id: 'talk-spoken-text', component: SpokenTextButton }
]

export const stateListeners = [stateListener]

export const messageListeners = [narrator]

export const servicesTabs = [{ id: 'talk', label: 'Talk', component: ServicesTab }]
