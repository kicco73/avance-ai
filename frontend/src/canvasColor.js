// iOS 26 standalone webapps have a regression (WebKit #301108, separate
// from the keyboard-viewport bug useVisualViewport.js works around) where
// the layout viewport doesn't extend under the home indicator despite
// viewport-fit=cover — that strip is painted by WebKit with the document
// canvas's own background-color, which no element's own CSS can reach.
// The only lever left is keeping <html>'s inline background-color in sync
// with whatever color the visible screen's own bottom edge actually is,
// so the unreachable strip reads as a continuation of it instead of a
// mismatched gap. App.vue's html, body rule still sets the static
// #9aa1ac fallback in its background shorthand — these functions only
// ever add a more specific inline style on top of that, never replace it
// for good, so a screen that never calls setCanvasColor still gets that
// same base color underneath.

export function setCanvasColor(color) {
  const previous = document.documentElement.style.backgroundColor
  document.documentElement.style.backgroundColor = color
  return previous
}

export function restoreCanvasColor(previous) {
  document.documentElement.style.backgroundColor = previous
}
