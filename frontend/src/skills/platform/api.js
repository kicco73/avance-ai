// Platform's own routes, the analogue of src/api.js for this skill —
// everything under /api/skills/platform/…. They leave with the directory.
//
// It re-exports the core barrel as well, so a screen in here has one
// place to import from and never has to know which half a call belongs
// to. Core -> skill would be the other direction, and is forbidden.
export * from '../../api.js'
export * from './api/admin.js'
export * from './api/aiModels.js'
export * from './api/appStore.js'
export * from './api/envStore.js'
export * from './api/labeling.js'
export * from './api/projectEditor.js'
export * from './api/serverOps.js'
export * from './api/testSessions.js'
export * from './api/sessionsAdmin.js'
