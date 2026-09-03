import { ref } from 'vue'
import { getProjectFiles } from './api.js'

export const projectFiles = ref([])

export async function refreshProjectFiles(projectId) {
  try {
    projectFiles.value = (await getProjectFiles(projectId)).files
  } catch {
    // already surfaced via apiFetch
  }
}
