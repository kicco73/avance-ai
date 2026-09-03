// The active project's identifier registry — {namespace: {identifier:
// description}} — shared as one reactive ref across every open
// TriggerEditor instance, refreshed whenever a structural edit lands.
import { ref } from 'vue'
import { getIdentifiers } from './api.js'

export const identifierRegistry = ref({})

export async function refreshIdentifierRegistry(projectId) {
  try {
    identifierRegistry.value = await getIdentifiers(projectId)
  } catch {
    // already surfaced via apiFetch
  }
}
