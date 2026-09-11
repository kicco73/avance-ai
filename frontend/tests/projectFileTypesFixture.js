export const PROJECT_FILE_TYPES_PAYLOAD = {
  root_file_names: ['index.css', 'index.yml'],
  types: [
    { extension: '.yml', content_type: 'text/yaml', label: 'YAML', kind: 'definition', folder: '', text: true, max_upload_bytes: Number.MAX_SAFE_INTEGER },
    { extension: '.yaml', content_type: 'text/yaml', label: 'YAML', kind: 'definition', folder: '', text: true, max_upload_bytes: Number.MAX_SAFE_INTEGER },
    { extension: '.txt', content_type: 'text/plain', label: 'Text', kind: 'document', folder: 'behaviour', text: true, max_upload_bytes: Number.MAX_SAFE_INTEGER },
    { extension: '.md', content_type: 'text/markdown', label: 'Markdown', kind: 'document', folder: 'behaviour', text: true, max_upload_bytes: Number.MAX_SAFE_INTEGER },
    { extension: '.csv', content_type: 'text/csv', label: 'CSV', kind: 'data', folder: 'behaviour', text: true, max_upload_bytes: Number.MAX_SAFE_INTEGER },
    { extension: '.css', content_type: 'text/css', label: 'Stylesheet', kind: 'stylesheet', folder: 'aspect', text: true, max_upload_bytes: Number.MAX_SAFE_INTEGER },
    { extension: '.png', content_type: 'image/png', label: 'PNG image', kind: 'image', folder: 'aspect', text: false, max_upload_bytes: 5 * 1024 * 1024 },
    { extension: '.svg', content_type: 'image/svg+xml', label: 'SVG image', kind: 'image', folder: 'aspect', text: false, max_upload_bytes: 5 * 1024 * 1024 },
    { extension: '.mp3', content_type: 'audio/mpeg', label: 'MP3 audio', kind: 'audio', folder: 'aspect', text: false, max_upload_bytes: 15 * 1024 * 1024 },
  ],
}
