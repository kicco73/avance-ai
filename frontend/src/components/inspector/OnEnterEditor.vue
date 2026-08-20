<script setup>
// A CodeMirror-backed mini-editor for exactly one action's own on-enter
// script (see onEnterActions.js) — JavaScript syntax highlighting instead
// of TriggerEditor.vue's Python-expression namespace coloring/autocomplete
// (a different grammar entirely), otherwise the exact same narrow
// contract: two-way bound via defineModel, no persistence of its own —
// InspectorDetailCard.vue still owns editOnEnter/commitOnEnter, this only
// changes what renders the input itself.
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

// The two functions onEnterActions.js's own onEnterLocals actually expose
// (see that module) — wired in here by name/call-shape rather than
// introspected, since there's only ever this handful and each has its own
// argument shape worth spelling out (a snippet with real tab stops, not
// just the bare name). A future third local just needs its own entry
// added here, same as onEnterLocals itself.
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

// Same as TriggerEditor.vue's own editorSetup — codemirror's own
// basicSetup minus every gutter piece (lineNumbers/
// highlightActiveLineGutter/foldGutter): a short on-enter script has no
// use for a line-number column next to it. Deliberately *no* bare
// autocompletion() here (unlike TriggerEditor.vue's own copy of this
// array) — this script's own grammar is a fixed, known set of calls
// (see automaton_builder.py's own OnEnterScriptSignatureParser), never
// arbitrary JavaScript, so the *only* completion source this editor ever
// registers is completeOnEnterLocals below (see its own autocompletion({
// override: [...] }) in createEditor) — never @codemirror/lang-
// javascript's own generic keyword/local-variable completions, and never
// two competing autocompletion() configs to reason about.
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

// A change to `model` this editor's own updateListener didn't itself just
// cause — InspectorDetailCard.vue's own resetEditBuffers, switching this
// same still-open editor to a different action's own on-enter (see
// TriggerEditor.vue's own identical comment on this).
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
