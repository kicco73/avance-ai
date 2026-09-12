<script setup>
// What an administrator lands on: the project table, and the upload it
// owns. App.vue used to hold this, along with the five refs the upload
// needs and the dozen listeners the table emits — none of which the shell
// ever read. They came here with the screen they belong to.
import ManageProjectsView from './settings/ManageProjectsView.vue'
import { useProjectAdminActions } from '../useProjectAdminActions.js'

const props = defineProps({
  profile: { type: Object, default: null },
  // The navigation stack (see composables/useViewStack.js): this home
  // opens the screens it owns rather than asking the shell to name them.
  viewStack: { type: Object, required: true },
})

const emit = defineEmits(['open-chat', 'home', 'profile', 'logout'])

defineOptions({ inheritAttrs: false })

const {
  modelUploadInput, uploadingProject, uploadProgress, uploadProjectId, uploadIconReady,
  triggerModelUpload, handleNewProject, handleModelUploadChange, handleModelDownload,
  handleModelDelete, handlePublishProject, activateAndRefresh,
} = useProjectAdminActions()

// The embedded "Test" chat runs against the server-side active project,
// so opening Edit for a non-active project activates it first.
async function openEditor(projectId, buildError = null) {
  await activateAndRefresh(projectId)
  props.viewStack.pushView('edit', { projectId, buildError })
}

const listeners = {
  'new-project': handleNewProject,
  upload: triggerModelUpload,
  delete: handleModelDelete,
  edit: openEditor,
  label: (projectId) => props.viewStack.pushView('label', { projectId }),
  download: handleModelDownload,
  publish: handlePublishProject,
  'open-skill-view': (view, projectId) => props.viewStack.pushView(view, { projectId }),
  'manage-users': () => props.viewStack.pushView('manageUsers'),
  'manage-services': () => props.viewStack.pushView('services'),
  'app-store': () => props.viewStack.pushView('appStore'),
  chat: (projectId) => emit('open-chat', projectId),
  home: () => emit('home'),
  profile: () => emit('profile'),
  logout: () => emit('logout'),
}
</script>

<template>
  <ManageProjectsView
    :uploading="uploadingProject"
    :upload-progress="uploadProgress"
    :upload-project-id="uploadProjectId"
    :upload-icon-ready="uploadIconReady"
    role="admin"
    :profile="profile"
    v-on="listeners"
  />
  <input
    ref="modelUploadInput"
    type="file"
    accept=".zip"
    style="display: none"
    @change="handleModelUploadChange"
  />
</template>
