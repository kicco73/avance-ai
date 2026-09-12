<script setup>
// The text input and whatever side buttons this build has. The input is
// presentational — all chat business logic stays in the parent; each side
// button belongs to whoever contributed it.
import { ref } from 'vue'
import { chatInputControls } from '../../skills/registry.js'

defineProps({
  disabled: { type: Boolean, default: false },
  store: { type: Object, required: true },
  // A row that stands for a chat rather than being one — the frozen
  // previews of a project's design and of an app's page. It shows every
  // control this build has, because it is showing what a chat looks
  // like; a real row shows what its own conversation can reach (see
  // backend docs/BUS.md's own ui.services), which a sample has no way of
  // knowing and no business guessing.
  sample: { type: Boolean, default: false }
})

const draft = defineModel({ type: String, default: '' })

const emit = defineEmits(['submit'])

const inputRef = ref(null)

defineExpose({ focus: () => inputRef.value?.focus() })
</script>

<template>
  <form class="input-row" @submit.prevent="emit('submit')">
    <input
      ref="inputRef"
      v-model="draft"
      type="text"
      placeholder="Type a message..."
      :disabled="disabled"
      enterkeyhint="send"
      autocapitalize="sentences"
      autocomplete="off"
      spellcheck="true"
    />

    <component
      v-for="control in chatInputControls"
      :key="control.id"
      :is="control.component"
      :store="store"
      :disabled="disabled"
      :sample="sample"
    />
  </form>
</template>

<style scoped>
.input-row {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  /* Bottom safe area (home indicator / gesture nav bar) is reserved by
     the parent .chat-footer instead (see ChatView.vue) — a project's
     skin paints that element's background, so reserving it there lets
     a dark skin extend behind the home indicator instead of showing
     this row's own background through a color-mismatched gap. */
  border-top: 1px solid #ddd;
}

.input-row input {
  flex: 1;
  /* Without this a flex item won't shrink below its content's intrinsic
     width — with mic/audio/spoken-text all showing, that pushed the
     later buttons out of the row entirely on narrow screens instead of
     yielding space to them. */
  min-width: 0;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  /* 16px: below this, iOS Safari zooms the page in on focus and doesn't
     zoom back out on blur. */
  font-size: 1rem;
}
</style>

<style>
/* Unscoped, like AppHeader.vue's own control classes: each side button is
   contributed by whoever owns that capability and renders in its own
   scope, which a scoped selector here could never reach. The row owns how
   a control looks; the control owns what it does. */
.chat-input-control {
  flex: none;
  width: 2.5rem;
  height: 2.5rem;
  border-radius: 6px;
  border: 1px solid #999;
  background: white;
  color: #666;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.chat-input-control:disabled {
  opacity: 0.5;
  cursor: default;
}

.chat-input-control:hover:not(:disabled) {
  background: #f0f0f0;
}

/* No mouse precision to rely on — grows each side button to the ~44px
   minimum recommended touch target (iOS HIG / Material). */
@media (hover: none) and (pointer: coarse) {
  .chat-input-control {
    width: 2.75rem;
    height: 2.75rem;
  }
}
</style>
