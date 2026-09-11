import { ref } from 'vue'

// Whether this backend has the build package at all — GET /api/state
// carries `build_enabled` only where build/skill.py is installed (see
// its own module docstring). Manage projects' Publish button reads it:
// a backend that cannot compile still publishes, it just has nothing to
// compile the published revision into.
export const buildAvailable = ref(false)

export function setBuildAvailable(available) {
  buildAvailable.value = available
}
