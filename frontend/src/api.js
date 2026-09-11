// Every function here is implemented in api/<domain>.js, split along the
// same lines as the backend's own controllers — re-exported from this one
// file so existing `from '.../api.js'` imports never need to know about
// the split.
//
// Core routes only. A skill's own routes live in its own directory and
// leave with it (see skills/<key>/api.js); re-exporting them here would
// put a name the core may not say in a file every core module imports.
export * from './api/auth.js'
export * from './api/chat.js'
export * from './api/projects.js'
export * from './api/serverAdmin.js'
export * from './api/metrics.js'
export * from './api/aiModels.js'
export * from './api/skills.js'
