// Which model answers, and who may change it.
//
// Reading the roster is the deployment describing itself, and the core
// does that through /api/core/settings/services. *Choosing* is an
// operation somebody performs from a panel, so it arrives from whoever
// contributed one — a leaf, because a low core module must not import
// the registry.
//
// The null object is a build with no panel: nothing to switch, and the
// live chat simply runs on whatever its configuration named. Screens ask
// `available` before offering the choice at all.
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
