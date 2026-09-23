import { afterEach, describe, expect, it } from 'vitest'
import { createCrepe } from '../markdownCrepe.js'

describe('a markdown file opened and saved in the editor', () => {
  let crepe

  afterEach(() => {
    crepe?.destroy()
  })

  async function roundTrip(doc) {
    const root = document.createElement('div')
    document.body.appendChild(root)
    crepe = createCrepe({ root, defaultValue: doc })
    await crepe.create()
    return crepe.getMarkdown().trimEnd()
  }

  it.each([
    'Hola %(nombre_usuario)s',
    'Hola %{nombre_usuario}',
    '- uno_dos_tres',
  ])('keeps an underscore inside a word as it was: %s', async (doc) => {
    expect(await roundTrip(doc)).toBe(doc)
  })

  it('still escapes what would otherwise read as emphasis', async () => {
    expect(await roundTrip('\\_not emphasis\\_ and a\\*b')).toBe('\\_not emphasis\\_ and a\\*b')
  })
})
