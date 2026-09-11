import { ref } from 'vue'
import { getProjectFileTypes } from './api.js'

class ProjectFileType {
  constructor(payload) {
    this.extension = payload.extension
    this.contentType = payload.content_type
    this.label = payload.label
    this.kind = payload.kind
    this.folder = payload.folder
    this.text = payload.text
    this.maxUploadBytes = payload.max_upload_bytes
  }

  get isImage() {
    return this.kind === 'image'
  }

  get isAudio() {
    return this.kind === 'audio'
  }

  get hasEditor() {
    return this.text
  }

  get filed() {
    return this.folder !== ''
  }

  canonicalName(fileName) {
    return `${this.folder}/${fileName}`
  }

  oversized(size) {
    return size > this.maxUploadBytes
  }
}

const UNKNOWN_FILE_TYPE = new ProjectFileType({
  extension: '', content_type: 'application/octet-stream', label: 'File', kind: 'unknown',
  folder: '', text: false, max_upload_bytes: 0,
})

class ProjectFileTypeCatalog {
  constructor(payload) {
    this.rootFileNames = payload.root_file_names
    this.types = payload.types.map((type) => new ProjectFileType(type))
    this._byExtension = new Map(this.types.map((type) => [type.extension, type]))
  }

  of(fileName) {
    const dot = fileName.lastIndexOf('.')
    const extension = dot === -1 ? '' : fileName.slice(dot).toLowerCase()
    return this._byExtension.get(extension) ?? UNKNOWN_FILE_TYPE
  }

  isRootFile(fileName) {
    return this.rootFileNames.includes(fileName)
  }

  accepts(fileName) {
    return this.isRootFile(fileName) || this.of(fileName).filed
  }

  canonicalUploadName(fileName) {
    return this.isRootFile(fileName) ? fileName : this.of(fileName).canonicalName(fileName)
  }

  oversized(file) {
    return this.of(file.name).oversized(file.size)
  }

  isImage(fileName) {
    return this.of(fileName).isImage
  }

  isAudio(fileName) {
    return this.of(fileName).isAudio
  }

  hasEditor(fileName) {
    return this.of(fileName).hasEditor
  }

  livesIn(folder, fileName) {
    return this.of(fileName).folder === folder
  }

  get uploadableExtensions() {
    return this.types.filter((type) => type.filed).map((type) => type.extension)
  }

  get uploadAccept() {
    return [...new Set(this.types.map((type) => type.extension))].join(',')
  }

  get uploadableDescription() {
    return [...this.rootFileNames, ...this.uploadableExtensions].join(', ')
  }

  uploadLimitLabel(fileName) {
    return `${Math.round(this.of(fileName).maxUploadBytes / (1024 * 1024))} MB`
  }
}

const EMPTY_CATALOG = new ProjectFileTypeCatalog({ root_file_names: [], types: [] })

export const projectFileTypes = ref(EMPTY_CATALOG)

let loading = null

export function ensureProjectFileTypes() {
  if (loading === null) {
    loading = getProjectFileTypes()
      .then((payload) => { projectFileTypes.value = new ProjectFileTypeCatalog(payload) })
      .catch(() => { loading = null })
  }
  return loading
}
