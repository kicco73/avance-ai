<script setup>
// CodeMirror-backed editor for a single trigger/env `value` expression,
// two-way bound via defineModel with no persistence of its own. Adds
// autocomplete and per-namespace syntax coloring on top of plain CodeMirror.
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorState } from '@codemirror/state'
import {
  Decoration,
  EditorView,
  MatchDecorator,
  ViewPlugin,
  crosshairCursor,
  drawSelection,
  dropCursor,
  highlightActiveLine,
  highlightSpecialChars,
  keymap,
  rectangularSelection,
  tooltips
} from '@codemirror/view'
import { bracketMatching, defaultHighlightStyle, foldKeymap, indentOnInput, syntaxHighlighting } from '@codemirror/language'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { highlightSelectionMatches, searchKeymap } from '@codemirror/search'
import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from '@codemirror/autocomplete'
import { lintKeymap } from '@codemirror/lint'
import { identifierRegistry, refreshIdentifierRegistry } from '../../identifierRegistry.js'
import { projectFiles } from '../../projectFiles.js'
import { NAMESPACE_COLORS, REFERENCE_PATTERN_SOURCE, completeFilePathArgument, completeIdentifiers as completeIdentifiersFor, excludingNamespaces } from '../../triggerEditorSupport.js'

const model = defineModel({ type: String, default: '' })
const props = defineProps({ excludeNamespaces: { type: Array, default: () => [] } })
const emit = defineEmits(['blur'])

const loading = ref(true)
const editorHost = ref(null)
let view = null

// Reads identifierRegistry.value fresh on every call rather than a local
// snapshot, so an identifier added elsewhere while this editor is open is
// visible on the very next keystroke.
function completeIdentifiers(context) {
  return completeIdentifiersFor(context, excludingNamespaces(identifierRegistry.value, props.excludeNamespaces))
}

function completeFilePath(context) {
  return completeFilePathArgument(context, projectFiles.value)
}

// Matches a namespace reference (e.g. "signal.mood") — group 1 is the
// namespace path, used to look up its color. A fresh RegExp per mount:
// the /g flag makes a shared instance carry lastIndex across instances.
const namespaceMatcher = new MatchDecorator({
  regexp: new RegExp(REFERENCE_PATTERN_SOURCE, 'g'),
  decoration: (match) => {
    const color = NAMESPACE_COLORS[match[1]]
    return color ? Decoration.mark({ attributes: { style: `color: ${color}` } }) : Decoration.none
  }
})

const namespaceHighlighter = ViewPlugin.fromClass(
  class {
    constructor(cmView) {
      this.decorations = namespaceMatcher.createDeco(cmView)
    }

    update(update) {
      this.decorations = namespaceMatcher.updateDeco(update, this.decorations)
    }
  },
  { decorations: (instance) => instance.decorations }
)

// Same as CodeMirror's basicSetup, minus gutter pieces (lineNumbers etc.)
// — a one-line expression needs no line-number column, and dropping just
// lineNumbers would still leave an empty gutter strip.
const editorSetup = [
  highlightSpecialChars(),
  history(),
  drawSelection(),
  dropCursor(),
  EditorState.allowMultipleSelections.of(true),
  indentOnInput(),
  syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
  bracketMatching(),
  closeBrackets(),
  autocompletion(),
  rectangularSelection(),
  crosshairCursor(),
  highlightActiveLine(),
  highlightSelectionMatches(),
  keymap.of([
    ...closeBracketsKeymap,
    ...defaultKeymap,
    ...searchKeymap,
    ...historyKeymap,
    ...foldKeymap,
    ...completionKeymap,
    ...lintKeymap
  ])
]

function createEditor() {
  view = new EditorView({
    doc: model.value,
    parent: editorHost.value,
    extensions: [
      editorSetup,
      EditorView.lineWrapping,
      autocompletion({ override: [completeIdentifiers, completeFilePath] }),
      namespaceHighlighter,
      // Without this, the completion tooltip's container defaults to the
      // editor's own DOM (see @codemirror/view's TooltipViewManager),
      // which .trigger-editor-host clips via overflow: hidden — the
      // dropdown was getting cut off instead of floating over the rest
      // of the Inspector. Escaping to <body> needs the standalone
      // .cm-tooltip z-index rule below to still land above the panel.
      tooltips({ parent: document.body }),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) model.value = update.state.doc.toString()
      }),
      EditorView.domEventHandlers({ blur: () => emit('blur') })
    ]
  })
}

function setEditorDoc(newContent) {
  if (!view) return
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: newContent } })
}

// Handles a model change this editor's own updateListener didn't cause —
// e.g. the caller switching this still-open editor to a different
// expression without remounting it.
watch(model, (newValue) => {
  if (view && newValue !== view.state.doc.toString()) setEditorDoc(newValue)
})

onMounted(async () => {
  // identifierRegistry is populated elsewhere; completion sources read it
  // live at call time, so this component never needs its own fetch.
  loading.value = false
  await nextTick()
  createEditor()
})

onBeforeUnmount(() => {
  view?.destroy()
  view = null
})
</script>

<template>
  <div class="trigger-editor">
    <p v-if="loading" class="trigger-editor-status">Loading…</p>
    <div v-show="!loading" ref="editorHost" class="trigger-editor-host" @click.stop></div>
  </div>
</template>

<style scoped>
.trigger-editor { display: block; }
.trigger-editor-status { margin: 0; padding: 0.35rem 0.5rem; font-size: 0.82rem; color: #666; }
.trigger-editor-host {
  border: 1px solid #ccc;
  border-radius: 6px;
  font-size: 0.82rem;
  overflow: hidden;
}
.trigger-editor-host :deep(.cm-editor) { outline: none; }
.trigger-editor-host :deep(.cm-content) { padding: 0.35rem 0.5rem; min-height: 3.2rem; }
.trigger-editor-host :deep(.cm-scroller) { font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; }
</style>

<style>
/* Unscoped: tooltips({ parent: document.body }) above (see
   createEditor) renders every completion tooltip — the list and this
   "info" side-panel alike — straight onto <body>, outside this
   component's DOM subtree, so a scoped style's data-v-* selector would
   never match it. z-index bumped past the Inspector panel/cards
   (highest existing value in this app short of a real modal is 1000)
   now that it's a body-level sibling instead of clipped inside
   .trigger-editor-host. */
.cm-tooltip {
  z-index: 1500;
}
.cm-trigger-completion-info {
  padding: 0.4rem 0.5rem;
  max-width: 22rem;
  font-size: 0.8rem;
  line-height: 1.35;
}
.cm-trigger-completion-info-header {
  display: flex;
  align-items: baseline;
  gap: 0.3rem;
  font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace;
}
.cm-trigger-completion-info-symbol {
  color: #888;
  font-size: 0.75rem;
}
.cm-trigger-completion-info-description {
  margin-top: 0.25rem;
  color: #333;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
