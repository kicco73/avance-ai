import { projectFileTypes } from './projectFileTypes.js'

export function findIconFile(files) {
  return files.find((name) => name.startsWith('media/icon.') && projectFileTypes.value.isImage(name)) ?? null
}
