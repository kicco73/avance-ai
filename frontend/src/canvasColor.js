
export function setCanvasColor(color) {
  const previous = document.documentElement.style.backgroundColor
  document.documentElement.style.backgroundColor = color
  return previous
}

export function restoreCanvasColor(previous) {
  document.documentElement.style.backgroundColor = previous
}
