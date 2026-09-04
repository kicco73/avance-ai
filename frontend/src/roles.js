// Mirrors backend/src/auth/roles.py exactly — same hierarchy, same
// comparison, so a frontend gate and its backend enforcement never disagree.
const ROLE_LEVELS = { user: 0, customer: 1, supervisor: 2, admin: 3 }

export function roleSatisfies(userRole, requiredRole) {
  return (ROLE_LEVELS[userRole] ?? -1) >= (ROLE_LEVELS[requiredRole] ?? 0)
}
