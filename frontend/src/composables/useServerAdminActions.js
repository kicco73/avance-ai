import { getAbout } from '../api.js'
import { aboutDialog } from '../dialogStore.js'

export function useServerAdminActions() {
  async function handleShowAbout() {
    try {
      const about = await getAbout()
      await aboutDialog({ version: about.version })
    } catch {
    }
  }

  return { handleShowAbout }
}
