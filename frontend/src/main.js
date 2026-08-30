import { createApp } from 'vue'
import App from './App.vue'
import { installViewportRecovery } from './useVisualViewport.js'

installViewportRecovery()
createApp(App).mount('#app')
