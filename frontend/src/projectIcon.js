// The backend accepts any of these image extensions under aspect/ (see
// backend/src/project/archive/layout.py's IMAGE_CONTENT_TYPE_BY_EXTENSION).
const ICON_FILE_RE = /^aspect\/icon\.(png|jpe?g|gif|webp|svg)$/i

export function findIconFile(files) {
  return files.find((name) => ICON_FILE_RE.test(name)) ?? null
}
