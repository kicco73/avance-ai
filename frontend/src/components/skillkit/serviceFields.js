export function fieldLabel(key) {
  return key.replace(/-/g, ' ').replace(/^./, (c) => c.toUpperCase())
}
