export const ASPECTS = [
  { id: 'dynamic', label: 'Dynamic' },
  { id: 'web', label: 'Web', width: 800, height: 600 },
  { id: 'mobile-vertical', label: 'Mobile vertical', width: 393, height: 852 },
  { id: 'mobile-horizontal', label: 'Mobile horizontal', width: 852, height: 393 },
]

export function aspectFor(id) {
  return ASPECTS.find((a) => a.id === id) ?? ASPECTS[0]
}
