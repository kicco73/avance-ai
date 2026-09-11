// CodeMirror 6 extension: an inline clickable swatch next to every color
// token (hex/rgb/rgba/hsl/hsla) in a CSS buffer. Clicking one opens the
// browser's native color picker (an <input type="color"> IS the swatch —
// no custom dialog needed); picking a color replaces that exact token with
// the browser's own hex output. Only hex round-trips its original width —
// rgb()/hsl() input always comes back out as 6-digit hex, since that's the
// only format the input element itself can hold (no alpha channel either).
import { Decoration, EditorView, MatchDecorator, ViewPlugin, WidgetType } from '@codemirror/view'

const COLOR_PATTERN = /#(?:[0-9a-fA-F]{3,4}){1,2}\b|\b(?:rgb|rgba|hsl|hsla)\([^)]*\)/g

function clamp255(n) {
  return Math.max(0, Math.min(255, Math.round(n)))
}

function componentToHex(n) {
  return clamp255(n).toString(16).padStart(2, '0')
}

function hslToRgb(h, s, l) {
  const hue = (((h % 360) + 360) % 360) / 360
  const sat = Math.max(0, Math.min(1, s / 100))
  const lig = Math.max(0, Math.min(1, l / 100))
  if (sat === 0) {
    const v = lig * 255
    return [v, v, v]
  }
  const q = lig < 0.5 ? lig * (1 + sat) : lig + sat - lig * sat
  const p = 2 * lig - q
  const hueToRgb = (t) => {
    if (t < 0) t += 1
    if (t > 1) t -= 1
    if (t < 1 / 6) return p + (q - p) * 6 * t
    if (t < 1 / 2) return q
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
    return p
  }
  return [hueToRgb(hue + 1 / 3) * 255, hueToRgb(hue) * 255, hueToRgb(hue - 1 / 3) * 255]
}

function parseRgbComponent(raw) {
  return raw.endsWith('%') ? parseFloat(raw) * 2.55 : parseFloat(raw)
}

// Normalizes any token COLOR_PATTERN can match into a 6-digit hex string —
// the only shape <input type="color"> accepts as its value.
export function colorTokenToHex(token) {
  if (token[0] === '#') {
    let hex = token.slice(1)
    if (hex.length === 3 || hex.length === 4) hex = hex.slice(0, 3).split('').map((c) => c + c).join('')
    else hex = hex.slice(0, 6)
    return `#${hex.padEnd(6, '0')}`
  }
  const args = token.slice(token.indexOf('(') + 1, token.lastIndexOf(')')).split(/[\s,/]+/).filter(Boolean)
  if (token.startsWith('hsl')) {
    const [r, g, b] = hslToRgb(parseFloat(args[0]), parseFloat(args[1]), parseFloat(args[2]))
    return `#${componentToHex(r)}${componentToHex(g)}${componentToHex(b)}`
  }
  const [r, g, b] = args.map(parseRgbComponent)
  return `#${componentToHex(r)}${componentToHex(g)}${componentToHex(b)}`
}

class ColorSwatchWidget extends WidgetType {
  constructor(token, from, to) {
    super()
    this.token = token
    this.from = from
    this.to = to
  }

  eq(other) {
    return other.token === this.token && other.from === this.from && other.to === this.to
  }

  toDOM(view) {
    const input = document.createElement('input')
    input.type = 'color'
    input.className = 'cm-color-swatch'
    input.title = this.token
    try {
      input.value = colorTokenToHex(this.token)
    } catch {
      input.value = '#000000'
    }
    // mousedown, not click — CodeMirror's own selection handling would
    // otherwise steal focus before the native picker gets to open.
    input.addEventListener('mousedown', (event) => event.stopPropagation())
    input.addEventListener('input', () => {
      view.dispatch({ changes: { from: this.from, to: this.to, insert: input.value } })
    })
    return input
  }

  ignoreEvent() {
    return true
  }
}

const colorMatcher = new MatchDecorator({
  regexp: COLOR_PATTERN,
  decoration: (match, _view, pos) =>
    Decoration.widget({ widget: new ColorSwatchWidget(match[0], pos, pos + match[0].length), side: -1 })
})

const colorSwatchPlugin = ViewPlugin.fromClass(
  class {
    constructor(view) {
      this.decorations = colorMatcher.createDeco(view)
    }
    update(update) {
      this.decorations = colorMatcher.updateDeco(update, this.decorations)
    }
  },
  { decorations: (instance) => instance.decorations }
)

const colorSwatchTheme = EditorView.baseTheme({
  '.cm-color-swatch': {
    width: '0.9em',
    height: '0.9em',
    margin: '0 2px',
    padding: 0,
    border: '1px solid rgba(0, 0, 0, 0.25)',
    borderRadius: '3px',
    verticalAlign: 'middle',
    cursor: 'pointer',
    background: 'none'
  },
  '.cm-color-swatch::-webkit-color-swatch-wrapper': { padding: 0 },
  '.cm-color-swatch::-webkit-color-swatch': { border: 'none', borderRadius: '2px' },
  '.cm-color-swatch::-moz-color-swatch': { border: 'none', borderRadius: '2px' }
})

export const cssColorPicker = [colorSwatchPlugin, colorSwatchTheme]
