<script setup>
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
  lineNumbers,
  rectangularSelection,
  tooltips
} from '@codemirror/view'
import { bracketMatching, defaultHighlightStyle, foldKeymap, indentOnInput, syntaxHighlighting } from '@codemirror/language'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { highlightSelectionMatches, searchKeymap } from '@codemirror/search'
import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from '@codemirror/autocomplete'
import { lintKeymap } from '@codemirror/lint'
import { identifierRegistry, refreshIdentifierRegistry } from '../../identifierRegistry.js'
import { NAMESPACE_COLORS, REFERENCE_PATTERN_SOURCE, completeIdentifiers as completeIdentifiersFor, excludingNamespaces } from '../../triggerEditorSupport.js'

const model = defineModel({ type: String, default: '' })
const props = defineProps({
  excludeNamespaces: { type: Array, default: () => [] },
  large: { type: Boolean, default: false },
  tooltipParent: { type: Object, default: null }
})
const emit = defineEmits(['blur'])

const loading = ref(true)
const editorHost = ref(null)
let view = null

function completeIdentifiers(context) {
  return completeIdentifiersFor(context, excludingNamespaces(identifierRegistry.value, props.excludeNamespaces))
}

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
      props.large ? lineNumbers() : [],
      props.large ? keymap.of([indentWithTab]) : [],
      EditorView.lineWrapping,
      autocompletion({ override: [completeIdentifiers] }),
      namespaceHighlighter,
      tooltips({ parent: props.tooltipParent ?? document.body }),
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

watch(model, (newValue) => {
  if (view && newValue !== view.state.doc.toString()) setEditorDoc(newValue)
})

onMounted(async () => {
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
    <div v-show="!loading" ref="editorHost" class="trigger-editor-host" :class="{ 'trigger-editor-host-large': large }" @click.stop></div>
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
.trigger-editor-host-large :deep(.cm-content) { min-height: 24rem; }
</style>

<style>
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
