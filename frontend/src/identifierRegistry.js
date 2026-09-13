import { ref } from 'vue'
import { getIdentifiers } from './api.js'

export const identifierRegistry = ref({})

export async function refreshIdentifierRegistry(projectId) {
  try {
    identifierRegistry.value = await getIdentifiers(projectId)
  } catch {
  }
}
