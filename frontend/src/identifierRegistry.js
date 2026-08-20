// The active project's own identifier registry (see api.js's
// getIdentifiers/backend automaton.identifier_registry.build_registry) —
// {namespace: {identifier: description}} — shared as one reactive ref
// across every open TriggerEditor.vue instance, rather than each one
// fetching (and locally caching) its own copy on mount. A signal/action
// added, renamed, or removed anywhere in the project is refreshed here
// once (see refreshIdentifierRegistry, called from EditProjectView.vue's
// own refreshValidStateKeys — the single point every structural edit AND
// every manual Code-editor save already funnels through) and every
// TriggerEditor's own completion source reads `identifierRegistry.value`
// live at call time, so it's never stale just because that particular
// action's own card happened to already be open when the edit landed.
import { ref } from 'vue'
import { getIdentifiers } from './api.js'

export const identifierRegistry = ref({})

// `projectName`, when given, overrides the backend's own default of
// "whichever project is currently active" — EditProjectView.vue's own
// caller always passes its own props.projectName explicitly, so a
// trigger editor open on project A never silently reflects whatever
// project B happens to be globally active right now.
export async function refreshIdentifierRegistry(projectName = null) {
  try {
    identifierRegistry.value = await getIdentifiers(projectName)
  } catch {
    // already surfaced via apiFetch
  }
}
