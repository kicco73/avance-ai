import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function getSkills() {
  return apiFetch(`${API_URL}/skills`)
}

// The services a project may declare a level for, as this backend has
// them installed — name, label and description all read off the server's
// own source tree. There is no endpoint for this subset: the skills
// collection already carries `declarable` on every row, so this filters
// what it has rather than asking a second question about the same rows.
export async function getDeclarableServices() {
  const { skills } = await getSkills()
  return { services: skills.filter((skill) => skill.declarable) }
}
