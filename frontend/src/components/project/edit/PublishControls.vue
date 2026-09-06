<script setup>
defineProps({
  projectRevision: { type: Object, required: true },
  publishing: { type: Boolean, default: false },
  publishUpToDate: { type: Boolean, default: false },
  canRevert: { type: Boolean, default: false },
  menuOpen: { type: Boolean, default: false }
})

const emit = defineEmits(['publish', 'revert', 'update:menuOpen'])

function pickPublish() {
  emit('update:menuOpen', false)
  emit('publish')
}

function pickRevert() {
  emit('update:menuOpen', false)
  emit('revert')
}
</script>

<template>
  <!-- useProjectPublishing's outside-click handler keys on .publish-split-btn -->
  <div class="publish-split-btn">
    <button
      class="publish-btn"
      :disabled="publishUpToDate || publishing"
      :title="`Draft revision ${projectRevision.revision} — published: ${projectRevision.published_revision ?? 'never'}`"
      @click="emit('publish')"
    >{{ publishing ? 'Publishing…' : `Rev. ${projectRevision.revision}` }}</button>
    <template v-if="canRevert">
      <button
        type="button"
        class="publish-menu-toggle"
        title="More publish options"
        @click="emit('update:menuOpen', !menuOpen)"
      >▾</button>
      <div v-if="menuOpen" class="publish-menu-dropdown">
        <button type="button" class="publish-menu-item" @click="pickPublish">Publish</button>
        <button
          type="button"
          class="publish-menu-item publish-menu-item-danger"
          @click="pickRevert"
        >Revert to rev. {{ projectRevision.published_revision }}</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.publish-split-btn {
  position: relative;
  display: flex;
  align-items: stretch;
}

.publish-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: white;
  cursor: pointer;
}

.publish-btn:has(+ .publish-menu-toggle) {
  border-right: none;
  border-top-right-radius: 0;
  border-bottom-right-radius: 0;
}

.publish-btn:hover:not(:disabled) {
  background: #256428;
}

.publish-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.publish-menu-toggle {
  padding: 0.4rem 0.5rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  border-top-left-radius: 0;
  border-bottom-left-radius: 0;
  background: #2e7d32;
  color: white;
  cursor: pointer;
  font-size: 0.7rem;
}

.publish-menu-toggle:hover {
  background: #256428;
}

.publish-menu-dropdown {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 20;
  min-width: 11rem;
  padding: 0.3rem;
  border-radius: 8px;
  border: 1px solid #ddd;
  background: white;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
}

.publish-menu-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.45rem 0.6rem;
  border: none;
  border-radius: 5px;
  background: none;
  font-size: 0.82rem;
  color: #333;
  cursor: pointer;
}

.publish-menu-item:hover {
  background: #f0f4fa;
}

.publish-menu-item-danger {
  color: #c62828;
  font-weight: 700;
}

.publish-menu-item-danger:hover {
  background: #fdecea;
}
</style>
