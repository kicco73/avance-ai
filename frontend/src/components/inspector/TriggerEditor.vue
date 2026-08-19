<script setup>
// A CodeMirror-backed mini-editor for exactly one action's own `trigger:`
// expression — replaces InspectorDetailCard.vue's own plain textarea for
// that one field. Deliberately narrow, unlike CodeEditor.vue (which owns
// a whole file's own load/save/undo/redo): this component only ever
// holds one expression's own text, two-way bound via defineModel, with
// no persistence of its own — InspectorDetailCard.vue still owns
// editTrigger/commitTrigger exactly as it did with the plain textarea,
// this only changes what renders the input itself.
//
// Two things on top of plain CodeMirror: autocomplete (namespace names,
// then — after a namespace's own dot — that namespace's own identifiers,
// sourced from the active project's own identifier registry, see
// api.js's getIdentifiers/automaton.identifier_registry.build_registry)
// and syntax coloring (one fixed color per namespace, decided here —
// the registry itself never carries styling).
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
  rectangularSelection
} from '@codemirror/view'
import { bracketMatching, defaultHighlightStyle, foldKeymap, indentOnInput, syntaxHighlighting } from '@codemirror/language'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { highlightSelectionMatches, searchKeymap } from '@codemirror/search'
import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from '@codemirror/autocomplete'
import { lintKeymap } from '@codemirror/lint'
import { identifierRegistry, refreshIdentifierRegistry } from '../../identifierRegistry.js'
import { NAMESPACE_COLORS, REFERENCE_PATTERN_SOURCE, completeIdentifiers as completeIdentifiersFor } from '../../triggerEditorSupport.js'

const model = defineModel({ type: String, default: '' })
const emit = defineEmits(['blur'])

const loading = ref(true)
const editorHost = ref(null)
let view = null

// The one completion source this editor's own autocompletion() (below)
// registers — basicSetup's own copy activates the extension but wires no
// source of its own (see triggerEditorSupport.js's own completeIdentifiers
// for the actual two-case logic — kept there, not here, so it has a real
// test against a plain CompletionContext, no live view/DOM needed). Reads
// identifierRegistry.value fresh on every call (see identifierRegistry.js)
// rather than a local snapshot, so a signal/action added elsewhere while
// this editor is already open and mounted is visible the very next
// keystroke, not just on the next open.
function completeIdentifiers(context) {
  return completeIdentifiersFor(context, identifierRegistry.value)
}

// Matches a complete namespace reference (e.g. "signal.mood",
// "session.metric.engagement") anywhere in the text — group 1 (see
// REFERENCE_PATTERN_SOURCE's own docstring) is the namespace path, used
// to look up its own fixed color. Doesn't match the trailing "()" a
// proxy reference is always called with — that's fine, left in the
// editor's own default color: it's already what visually marks a proxy
// namespace apart from a plain variable one (see this component's own
// docstring), no extra coloring needed on top. A fresh RegExp per mount
// (own /g flag — a shared instance would carry lastIndex state across
// unrelated editor instances).
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

// Same as codemirror's own basicSetup, minus every gutter piece
// (lineNumbers/highlightActiveLineGutter/foldGutter) — a one-line trigger
// expression has no use for a line-number column, and dropping lineNumbers
// alone would still leave the other two rendering their own empty gutter
// strip next to it.
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
      autocompletion({ override: [completeIdentifiers] }),
      namespaceHighlighter,
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

// A change to `model` this editor's own updateListener didn't itself
// just cause — InspectorDetailCard.vue's own resetEditBuffers, switching
// this same still-open editor to a different action's own trigger (see
// this component's own docstring: it isn't necessarily remounted on
// every selection change).
watch(model, (newValue) => {
  if (view && newValue !== view.state.doc.toString()) setEditorDoc(newValue)
})

onMounted(async () => {
  // Only fetches on this editor's own *first-ever* mount across the
  // whole app (an empty registry — see identifierRegistry.js's own
  // default) — every later open reuses whatever's already shared,
  // refreshed independently by every project edit (see
  // EditProjectView.vue's own refreshValidStateKeys), so reopening an
  // already-visited action's card never re-shows the loading placeholder
  // just to re-fetch data that hasn't gone stale.
  if (!Object.keys(identifierRegistry.value).length) await refreshIdentifierRegistry()
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
/* Unscoped: CodeMirror renders its own completion "info" panel (see
   triggerEditorSupport.js's own completionInfo) into a tooltip appended
   straight to <body>, outside this component's own DOM subtree — a
   scoped style's own data-v-* attribute selector would never match it. */
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
