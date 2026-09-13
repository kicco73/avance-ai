const noModelSelector = {
  available: false,
  models: { value: [] },
  auto: { value: true },
  currentIndex: { value: 0 },
  selectionLoading: { value: false },
  autoLabel: null,
  load: async () => {},
  select: async () => {},
  applyInfo: () => {},
}

let selector = noModelSelector

export function installModelSelector(contributions) {
  selector = contributions.length > 0 ? contributions[0] : noModelSelector
}

export function modelSelector() {
  return selector
}
