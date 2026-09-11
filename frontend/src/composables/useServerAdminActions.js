import { getAbout } from '../api.js'
import { aboutDialog } from '../dialogStore.js'

// What this deployment is, asked from any screen that offers an
// "About Avance..." item. Describing, so core — the operations that
// change a deployment (backup, restore, wipe, clean) left with the panel
// that performs them.
export function useServerAdminActions() {
  async function handleShowAbout() {
    try {
      const about = await getAbout()
      await aboutDialog({ version: about.version })
    } catch {
      // already surfaced via apiFetch
    }
  }

  return { handleShowAbout }
}
