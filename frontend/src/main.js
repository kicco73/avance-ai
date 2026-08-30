import { createApp } from 'vue'
import App from './App.vue'
import { installSafeAreaFallback, installViewportRecovery } from './useVisualViewport.js'

installSafeAreaFallback()
installViewportRecovery()
createApp(App).mount('#app')
