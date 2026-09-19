const installed = []

export function installTriggerNamespaces(namespaces) {
  installed.splice(0, installed.length, ...namespaces)
}

export function contributedTriggerNamespaces() {
  return installed
}
