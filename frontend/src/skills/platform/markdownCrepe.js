import { Crepe } from '@milkdown/crepe'
import { remarkStringifyOptionsCtx } from '@milkdown/kit/core'
import '@milkdown/crepe/theme/common/style.css'
import '@milkdown/crepe/theme/frame.css'

const INTRAWORD_ESCAPED_UNDERSCORE = /(?<=[\p{L}\p{N}])\\_(?=[\p{L}\p{N}])/gu

function text(node, _parent, state, info) {
  return state.safe(node.value, info).replace(INTRAWORD_ESCAPED_UNDERSCORE, '_')
}

export function createCrepe({ root, defaultValue }) {
  const crepe = new Crepe({
    root,
    defaultValue,
    features: { [Crepe.Feature.Latex]: false, [Crepe.Feature.TopBar]: true }
  })
  crepe.editor.config((ctx) => {
    ctx.update(remarkStringifyOptionsCtx, (options) => ({
      ...options, bullet: '-', handlers: { ...options.handlers, text }
    }))
  })
  return crepe
}
