const scopes = {}

export function registerEffectsScope(kind, el) {
  scopes[kind] = el
  return () => {
    if (scopes[kind] === el) delete scopes[kind]
  }
}

export function effectsScopeFor(kind) {
  return scopes[kind] ?? null
}
