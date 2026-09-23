import { createApp } from 'vue'
import App from './App.vue'
import './styles/base.css'
import './styles/markdownContent.css'
import { installViewportOvershoot, installViewportRecovery } from './useVisualViewport.js'

installViewportOvershoot()
installViewportRecovery()
createApp(App).mount('#app')
