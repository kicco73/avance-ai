import { ref } from 'vue'
import { getProjectFiles } from './api.js'

export const projectFiles = ref([])

export async function refreshProjectFiles(projectName) {
  try {
    projectFiles.value = (await getProjectFiles(projectName)).files
  } catch {
    // already surfaced via apiFetch
  }
}
