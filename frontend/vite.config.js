import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fxRecorder } from './fx-recorder.js'

export default defineConfig({
  plugins: [vue(), fxRecorder('/private/tmp/claude-501/-Users-enricocarniani-avance-ai/5e948c52-006a-451a-a472-b5f68b7a7b74/scratchpad/fx.log')],
  server: {
    port: 5173
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.js']
  }
})
