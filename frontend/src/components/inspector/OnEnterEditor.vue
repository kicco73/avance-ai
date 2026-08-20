<script setup>
// A CodeMirror-backed mini-editor for one action's on-enter script, using
// JavaScript syntax highlighting instead of TriggerEditor.vue's Python-expression
// coloring. Two-way bound via defineModel; InspectorDetailCard.vue still owns persistence.
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorState } from '@codemirror/state'
import {
  EditorView,
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
import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap, snippetCompletion } from '@codemirror/autocomplete'
import { lintKeymap } from '@codemirror/lint'
import { javascript } from '@codemirror/lang-javascript'

const model = defineModel({ type: String, default: '' })
const emit = defineEmits(['blur'])

// The functions onEnterActions.js's onEnterLocals exposes, wired in here by name
// rather than introspected, since each needs its own snippet with real tab stops,
// not just the bare name. A new local needs its own entry added here too.
const ON_ENTER_COMPLETIONS = [
  snippetCompletion('celebrate()', { label: 'celebrate', type: 'function', detail: 'confetti burst' }),
  snippetCompletion("notify('${title}', '${body}')", { label: 'notify', type: 'function', detail: 'toast — title, markdown body' })
]

function completeOnEnterLocals(context) {
  const word = context.matchBefore(/\w*/)
  if (!word || (word.from === word.to && !context.explicit)) return null
  return { from: word.from, options: ON_ENTER_COMPLETIONS }
}

const editorHost = ref(null)
let view = null

// Codemirror's basicSetup minus every gutter piece — a short on-enter script
// has no use for a line-number column. Deliberately no bare autocompletion():
// the grammar is a fixed set of calls, so completeOnEnterLocals is the only source.
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
      javascript(),
      autocompletion({ override: [completeOnEnterLocals] }),
      EditorView.lineWrapping,
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

// Handles `model` changing from outside this editor's own updateListener —
// e.g. InspectorDetailCard.vue's resetEditBuffers switching this still-open
// editor to a different action's on-enter script.
watch(model, (newValue) => {
  if (view && newValue !== view.state.doc.toString()) setEditorDoc(newValue)
})

onMounted(async () => {
  await nextTick()
  createEditor()
})

onBeforeUnmount(() => {
  view?.destroy()
  view = null
})
</script>

<template>
  <div ref="editorHost" class="on-enter-editor-host" @click.stop></div>
</template>

<style scoped>
.on-enter-editor-host {
  border: 1px solid #ccc;
  border-radius: 6px;
  font-size: 0.82rem;
  overflow: hidden;
}
.on-enter-editor-host :deep(.cm-editor) { outline: none; }
.on-enter-editor-host :deep(.cm-content) { padding: 0.35rem 0.5rem; min-height: 2rem; font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; }
.on-enter-editor-host :deep(.cm-scroller) { font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; }
</style>
