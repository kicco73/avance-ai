<script setup>
defineProps({
  projectRevision: { type: Object, required: true },
  canRevert: { type: Boolean, default: false },
  reverting: { type: Boolean, default: false },
  menuOpen: { type: Boolean, default: false }
})

const emit = defineEmits(['revert', 'update:menuOpen'])

function pickRevert() {
  emit('update:menuOpen', false)
  emit('revert')
}
</script>

<template>
  <!-- useProjectRevision's outside-click handler keys on .revision-menu -->
  <div class="revision-menu">
    <button
      type="button"
      class="revision-btn"
      :disabled="!canRevert || reverting"
      :title="`Draft revision ${projectRevision.revision} — published: ${projectRevision.published_revision ?? 'never'}`"
      @click="emit('update:menuOpen', !menuOpen)"
    >{{ reverting ? 'Reverting…' : `Rev. ${projectRevision.revision}` }} ▾</button>
    <div v-if="menuOpen" class="revision-menu-dropdown">
      <button
        type="button"
        class="revision-menu-item revision-menu-item-danger"
        @click="pickRevert"
      >Revert to rev. {{ projectRevision.published_revision }}</button>
    </div>
  </div>
</template>

<style scoped>
.revision-menu {
  position: relative;
  display: flex;
  align-items: stretch;
}

.revision-btn {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.4rem 0.8rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: white;
  cursor: pointer;
  font-size: 0.85rem;
}

.revision-btn:hover:not(:disabled) {
  background: #256428;
}

.revision-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.revision-menu-dropdown {
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

.revision-menu-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.45rem 0.6rem;
  border: none;
  border-radius: 5px;
  background: none;
  font-size: 0.82rem;
  cursor: pointer;
}

.revision-menu-item:hover {
  background: #f0f4fa;
}

.revision-menu-item-danger {
  color: #c62828;
  font-weight: 700;
}

.revision-menu-item-danger:hover {
  background: #fdecea;
}
</style>
