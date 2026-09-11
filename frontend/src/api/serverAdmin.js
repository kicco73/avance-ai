import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// Running the server, as opposed to authoring what it runs: the whole
// database in and out, wiping live conversations, clearing revisions
// nothing points at, what this deployment has configured, what the
// scheduler has queued, and what version is running. Core, on
// /api/skills/platform/settings/… — an operator needs these whether or not an
// editor was ever installed (see system/server_admin_controller.py).

// Settings > "About Avance..." dialog — {name, version}, version being
// whatever the running backend's own __version__ (main.py) currently is.
export function getAbout() {
  return apiFetch(`${API_URL}/core/settings/about`)
}





// Settings > Manage services — read-only snapshot of .config.yml's own
// service sections (see backend AppConfig.public_services_snapshot).
export function getServicesConfig() {
  return apiFetch(`${API_URL}/core/settings/services`)
}

// Settings > Manage services > AI — each provider's own daily token
// spend, fetched once when the panel opens (see db/ai_usage.py):
// {today: {label: tokens}, today_cache_read: {label: tokens},
// history: [{timestamp, values: {label: tokens}, cache_read: {label: tokens}}, ...],
// cache_read_ratio: {label: 0..1}}.
export function getAiUsage() {
  return apiFetch(`${API_URL}/core/settings/services/ai-usage`)
}

// Settings > Manage services > Scheduler — Task rows for one status at a
// time, by run_at per `order` (see db/tasks.py's list_tasks).
export function getScheduledTasks(status, order = 'asc') {
  const params = new URLSearchParams({ status, order })
  return apiFetch(`${API_URL}/core/settings/tasks?${params}`)
}
