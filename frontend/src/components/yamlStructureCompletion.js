// CodeMirror completion source for index.yml's own structure — suggests
// known field names (`name`, `trigger`, `attachments`, ...) while the
// cursor sits on a new/partial key, based on which schema node the key's
// indentation-derived ancestors place it in (root, a state, an action list
// item, a signal, or init-action). Registered as YAML language data (see
// CodeEditor.vue), alongside yamlAttachmentCompletion.js's file-name
// completion — the two cover different things (field names vs. attachment
// values) and can run side by side.
//
// Field lists mirror backend/src/docs/PROJECT_SPECS.md (§2 top-level, §2.1
// project, §4 signals, §5 states, §6 actions, §8 init-action). `nested:
// true` marks fields whose value is itself a mapping/list, so the
// inserted text omits the trailing space a scalar field gets.
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
  { name: 'signal-tracking-on-ai-message' }
]

const STATE_FIELDS = [
  { name: 'contextual-prompt' },
  { name: 'fixed-message' },
  { name: 'ui-label' },
  { name: 'ui-description' },
  { name: 'actions', nested: true },
  { name: 'chat' },
  { name: 'history-cutoff' },
  { name: 'transition-log-level' },
  { name: 'attachments', nested: true }
]

const ACTION_FIELDS = [
  { name: 'name' },
  { name: 'target' },
  { name: 'trigger' },
  { name: 'on-enter' },
  { name: 'env', nested: true },
  { name: 'ui-label' },
  { name: 'ui-button' },
  { name: 'ui-description' },
  { name: 'attachments', nested: true }
]

const INIT_ACTION_FIELDS = [
  { name: 'target' },
  { name: 'on-enter' }
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

// Resolves "what schema node contains this position" from indentation
// alone (no AST/parser — same heuristic style as
// yamlAttachmentCompletion.js's isInsideAttachmentsBlock, generalized to
// build a full ancestor chain instead of checking a single parent key).
class YamlStructurePathResolver {
  constructor(doc) {
    this.doc = doc
  }

  // Walks upward from `lineNumber`, collecting enclosing mapping keys and
  // list items whose indentation strictly encloses `indent`. Returns the
  // ancestor chain, innermost first — e.g. for a line inside an action
  // entry: [{ type: 'list-item', indent: 6 }, { type: 'key', key: 'actions', indent: 4 }, ...].
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

// Fields for a new `key:` on its own line, given the ancestor chain for
// that line's own indentation.
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

// Fields for a fresh `- ` list item, given the ancestor chain resolved at
// the dash's own indentation (i.e. the chain describes the list itself,
// not an item within it).
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
