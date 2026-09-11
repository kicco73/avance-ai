<script setup>
import { ref } from 'vue'
import '../../styles/headerMenu.css'
import { useOutsideClickClose } from '../../composables/useOutsideClickClose.js'

const emit = defineEmits(['new-project', 'upload'])

const rootEl = ref(null)
const { open, toggle, close } = useOutsideClickClose(rootEl)

function selectNewProject() {
  close()
  emit('new-project')
}

function selectUploadProject() {
  close()
  emit('upload')
}
</script>

<template>
  <div ref="rootEl" class="header-menu">
    <button type="button" class="header-menu-btn" title="Add project" @click="toggle">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M12 4a1 1 0 0 1 1 1v6h6a1 1 0 1 1 0 2h-6v6a1 1 0 1 1-2 0v-6H5a1 1 0 1 1 0-2h6V5a1 1 0 0 1 1-1z" />
      </svg>
    </button>
    <Transition name="header-menu-panel">
      <div v-if="open" class="header-menu-panel">
        <ul class="add-project-list">
          <li>
            <button type="button" class="add-project-item" @click="selectNewProject">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                <path d="M12 4a1 1 0 0 1 1 1v6h6a1 1 0 1 1 0 2h-6v6a1 1 0 1 1-2 0v-6H5a1 1 0 1 1 0-2h6V5a1 1 0 0 1 1-1z" />
              </svg>
              <span>New project</span>
            </button>
          </li>
          <li>
            <button type="button" class="add-project-item" @click="selectUploadProject">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                <path d="M12 21a1 1 0 0 1-1-1v-9.59l-2.3 2.3a1 1 0 1 1-1.4-1.42l4-4a1 1 0 0 1 1.4 0l4 4a1 1 0 1 1-1.4 1.42l-2.3-2.3V20a1 1 0 0 1-1 1zM5 5a1 1 0 0 1 1-1h12a1 1 0 1 1 0 2H6a1 1 0 0 1-1-1z" />
              </svg>
              <span>Import project...</span>
            </button>
          </li>
        </ul>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.add-project-list {
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
}

.add-project-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.9rem;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.85rem;
  color: #4a6fa5;
}

.add-project-item:hover {
  background: #f0f4fa;
}

.add-project-item svg {
  flex-shrink: 0;
  color: #4a6fa5;
}
</style>
