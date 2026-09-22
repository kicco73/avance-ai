import { defineAsyncComponent } from 'vue'
import { customDialog } from './dialogStore.js'

const MediaDialog = defineAsyncComponent(() => import('./components/MediaDialog.vue'))

export function openMediaDialog(url) {
  customDialog({ component: MediaDialog, props: { url }, wide: true })
}
