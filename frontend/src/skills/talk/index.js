import { defineAsyncComponent } from 'vue'
import AudioToggleButton from './components/AudioToggleButton.vue'
import SpokenTextButton from './components/SpokenTextButton.vue'
import { stateListener } from './availability.js'
import { narrator } from './narrator.js'

const ServicesTab = defineAsyncComponent(() => import('./components/ServicesTab.vue'))

export const key = 'talk'

export const chatInputControls = [
  { id: 'talk-audio', component: AudioToggleButton },
  { id: 'talk-spoken-text', component: SpokenTextButton }
]

export const stateListeners = [stateListener]

export const messageListeners = [narrator]

export const servicesTabs = [{ id: 'talk', label: 'Talk', component: ServicesTab }]
