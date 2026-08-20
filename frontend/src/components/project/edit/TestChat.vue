<script setup>
// "Test" mode's own markup — the embedded live chat, full height (see
// EditProjectView.vue's own docstring on `mode`: 'edit'/'test'/'auto' are
// mutually exclusive now, so this never has to share space with Design's
// own split-view the way it used to). Extracted straight out of
// EditProjectView.vue's own template — purely presentational, same
// contract as ProjectDesignPanel.vue: every piece of state here not
// pulled straight from chatStore.js's own shared singleton is a prop,
// every user action reaching back into EditProjectView.vue's own
// `selected`-driven Inspector state is an emit.
//
// autoTrackingEnabled/autoTrackingLoading/toggleAutoTracking/handleReset/
// spokenTextEnabled are read straight from chatStore.js instead of being
// prop-drilled — they're a plain module-level store, not view-specific
// state, so there's nothing EditProjectView.vue needs to mediate here.
import ChatWindow from '../../chat/ChatWindow.vue'
import ChatTimeline from '../../chat/ChatTimeline.vue'
import RestartFromHereButton from '../../chat/RestartFromHereButton.vue'
import ModelMenu from '../../ModelMenu.vue'
import { autoTrackingEnabled, autoTrackingLoading, toggleAutoTracking, handleReset, spokenTextEnabled } from '../../../chatStore.js'

defineProps({
  timeline: { type: Array, required: true },
  signalsLog: { type: Array, default: () => [] },
  selected: { type: Object, default: null },
  // EditProjectView.vue's own stateLabelFor/isStateGone — pure functions
  // over state (availableStates/validStateKeys) that stays owned there,
  // same function-as-prop pattern ChatTimeline.vue's own resolve-state-
  // label prop already used before this extraction.
  resolveStateLabel: { type: Function, required: true },
  // EditProjectView.vue's own actionLabelFor(stateKey, actionName) — see
  // ChatTimeline.vue's own resolve-action-label prop.
  resolveActionLabel: { type: Function, required: true },
  isStateGone: { type: Function, required: true }
})

const emit = defineEmits(['select-message', 'select-transition', 'restart-prefill', 'restart-resend'])
</script>

<template>
  <div class="project-test-panel">
    <div class="edit-project-chat-panel">
      <div class="edit-project-chat-toolbar">
        <label
          class="dev-mode-toggle"
          :class="{ 'dev-mode-toggle-active': !autoTrackingEnabled, 'dev-mode-toggle-disabled': autoTrackingLoading }"
        >
          <input
            type="checkbox"
            :checked="!autoTrackingEnabled"
            :disabled="autoTrackingLoading"
            @change="toggleAutoTracking"
          />
          Dev mode: freeze automatic state transitions
        </label>
        <div class="edit-project-chat-toolbar-actions">
          <button class="reset-btn" @click="handleReset()">Reset</button>
          <ModelMenu />
        </div>
      </div>
      <ChatWindow>
        <template #timeline>
          <ChatTimeline
            :timeline="timeline"
            :signals-log="signalsLog"
            :selected="selected"
            :spoken-text-enabled="spokenTextEnabled"
            :resolve-state-label="resolveStateLabel"
            :resolve-action-label="resolveActionLabel"
            @select-message="emit('select-message', $event)"
            @select-transition="emit('select-transition', $event)"
          >
            <template #message-actions="{ message }">
              <RestartFromHereButton
                v-if="message.role === 'user'"
                :disabled="isStateGone(message)"
                @click="emit('restart-resend', message)"
                @double-click="emit('restart-prefill', message)"
              />
            </template>
          </ChatTimeline>
        </template>
      </ChatWindow>
    </div>
  </div>
</template>

<style scoped>
.project-test-panel { flex: 1; display: flex; flex-direction: column; min-width: 0; min-height: 0; }

.edit-project-chat-panel { flex: 1; min-height: 0; display: flex; flex-direction: column; min-width: 0; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }
.edit-project-chat-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; padding: 0.5rem 0.75rem; background: #f5f5f7; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.edit-project-chat-toolbar-actions { display: flex; align-items: center; gap: 0.5rem; }
.edit-project-chat-toolbar-actions .reset-btn { padding: 0.35rem 0.9rem; border-radius: 6px; border: 1px solid #c62828; background: white; color: #c62828; font-size: 0.85rem; cursor: pointer; }
.edit-project-chat-toolbar-actions .reset-btn:hover { background: #c62828; color: white; }

.dev-mode-toggle { display: flex; align-items: center; gap: 0.4rem; font-size: 0.82rem; color: #666; cursor: pointer; user-select: none; }
.dev-mode-toggle input { cursor: pointer; }
/* Same amber used elsewhere for "this changes normal behavior, pay
   attention" (see .inspector-detail-badge-current) — freezing
   transitions is a deliberate, temporary override, not the default. */
.dev-mode-toggle-active { color: #b06a00; font-weight: 600; }
.dev-mode-toggle-disabled { opacity: 0.6; cursor: not-allowed; }
.dev-mode-toggle-disabled input { cursor: not-allowed; }
</style>
