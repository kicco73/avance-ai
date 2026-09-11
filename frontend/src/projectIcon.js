import { projectFileTypes } from './projectFileTypes.js'

export function findIconFile(files) {
  return files.find((name) => name.startsWith('aspect/icon.') && projectFileTypes.value.isImage(name)) ?? null
}
