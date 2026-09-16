const TOP_LEVEL_FIELDS = [
  { name: 'avance-version' },
  { name: 'init-action', nested: true },
  { name: 'states', nested: true },
  { name: 'signals', nested: true },
  { name: 'general-prompt' },
  { name: 'attachments', nested: true },
  { name: 'project', nested: true }
]

const PROJECT_FIELDS = [
  { name: 'id' },
  { name: 'ui-label' },
  { name: 'ui-description' },
  { name: 'signal-tracking-on-ai-message' },
  { name: 'new-session-strategy' }
]

const STATE_FIELDS = [
  { name: 'contextual-prompt' },
  { name: 'fixed-message' },
  { name: 'ui-label' },
  { name: 'ui-description' },
  { name: 'actions', nested: true },
  { name: 'chat-enabled' },
  { name: 'history-cutoff' },
  { name: 'transition-log-level' },
  { name: 'signal-tracking-strategy' },
  { name: 'ai-memory-scope' },
  { name: 'attachments', nested: true },
  { name: 'ai-may-read-sources', nested: true },
  { name: 'ai-must-read-sources', nested: true },
  { name: 'ai-may-write-sources', nested: true }
]

const ACTION_FIELDS = [
  { name: 'name' },
  { name: 'target' },
  { name: 'trigger' },
  { name: 'task' },
  { name: 'on-exit' },
  { name: 'env', nested: true },
  { name: 'ui-label' },
  { name: 'ui-button' },
  { name: 'ui-description' },
  { name: 'attachments', nested: true }
]

const INIT_ACTION_FIELDS = [
  { name: 'target' },
  { name: 'task' },
  { name: 'on-exit' }
]

const SIGNAL_FIELDS = [
  { name: 'definition' },
  { name: 'ui-label' },
  { name: 'ui-description' },
  { name: 'attachments', nested: true }
]

const KEY_LINE_PATTERN = /^(\s*)([A-Za-z0-9_-]+):/
const LIST_ITEM_LINE_PATTERN = /^(\s*)-\s*(.*)$/
const NEW_KEY_PATTERN = /^(\s*)([A-Za-z0-9_-]*)$/
const NEW_LIST_ITEM_PATTERN = /^(\s*)-\s*([A-Za-z0-9_-]*)$/

class YamlStructurePathResolver {
  constructor(doc) {
    this.doc = doc
  }

  ancestorPath(lineNumber, indent) {
    const path = []
    let ceiling = indent
    for (let n = lineNumber - 1; n >= 1; n--) {
      const text = this.doc.line(n).text
      if (!text.trim()) continue

      const listMatch = LIST_ITEM_LINE_PATTERN.exec(text)
      if (listMatch) {
        const dashIndent = listMatch[1].length
        const itemIndent = dashIndent + 2
        if (itemIndent <= ceiling) {
          path.push({ type: 'list-item', indent: dashIndent })
          ceiling = dashIndent
        }
        continue
      }

      const keyMatch = KEY_LINE_PATTERN.exec(text)
      if (keyMatch) {
        const keyIndent = keyMatch[1].length
        if (keyIndent < ceiling) {
          path.push({ type: 'key', key: keyMatch[2], indent: keyIndent })
          ceiling = keyIndent
        }
      }
    }
    return path
  }
}

function fieldsForMappingContext(path) {
  const top = path[0]
  if (!top) return TOP_LEVEL_FIELDS
  if (top.type === 'list-item') {
    const parent = path[1]
    return parent?.key === 'actions' ? ACTION_FIELDS : null
  }
  if (top.key === 'init-action') return INIT_ACTION_FIELDS
  if (top.key === 'project') return PROJECT_FIELDS
  const parent = path[1]
  if (parent?.key === 'states') return STATE_FIELDS
  if (parent?.key === 'signals') return SIGNAL_FIELDS
  return null
}

function fieldsForListItemContext(path) {
  return path[0]?.key === 'actions' ? ACTION_FIELDS : null
}

function toOptions(fields) {
  return fields.map(({ name, nested }) => ({ label: name, type: 'property', apply: nested ? `${name}:` : `${name}: ` }))
}

export function yamlStructureCompletionSource() {
  return (context) => {
    const doc = context.state.doc
    const line = doc.lineAt(context.pos)
    const textBefore = line.text.slice(0, context.pos - line.from)
    const resolver = new YamlStructurePathResolver(doc)

    const listItemMatch = NEW_LIST_ITEM_PATTERN.exec(textBefore)
    if (listItemMatch) {
      const [, dashIndent, partial] = listItemMatch
      const fields = fieldsForListItemContext(resolver.ancestorPath(line.number, dashIndent.length))
      if (!fields) return null
      return { from: context.pos - partial.length, options: toOptions(fields), validFor: /^[A-Za-z0-9_-]*$/ }
    }

    const keyMatch = NEW_KEY_PATTERN.exec(textBefore)
    if (keyMatch) {
      const [, indent, partial] = keyMatch
      const fields = fieldsForMappingContext(resolver.ancestorPath(line.number, indent.length))
      if (!fields) return null
      return { from: context.pos - partial.length, options: toOptions(fields), validFor: /^[A-Za-z0-9_-]*$/ }
    }

    return null
  }
}
