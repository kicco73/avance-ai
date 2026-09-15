import { Crepe } from '@milkdown/crepe'
import { remarkStringifyOptionsCtx } from '@milkdown/kit/core'
import '@milkdown/crepe/theme/common/style.css'
import '@milkdown/crepe/theme/frame.css'

export function createCrepe({ root, defaultValue }) {
  const crepe = new Crepe({
    root,
    defaultValue,
    features: { [Crepe.Feature.Latex]: false, [Crepe.Feature.TopBar]: true }
  })
  crepe.editor.config((ctx) => { ctx.update(remarkStringifyOptionsCtx, (options) => ({ ...options, bullet: '-' })) })
  return crepe
}
