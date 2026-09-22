<script setup>
import ManageProjectsView from './settings/ManageProjectsView.vue'
import { useProjectAdminActions } from '../useProjectAdminActions.js'

const props = defineProps({
  profile: { type: Object, default: null },
  viewStack: { type: Object, required: true },
})

const emit = defineEmits(['open-chat', 'close', 'home', 'profile', 'logout', 'about'])

const {
  modelUploadInput, uploadingProject, uploadProgress, uploadProjectId, uploadIconReady,
  triggerModelUpload, handleNewProject, handleModelUploadChange, handleModelDownload,
  handleModelDelete, handlePublishProject, activateAndRefresh,
} = useProjectAdminActions()

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
  chat: (projectId) => emit('open-chat', projectId),
  about: () => emit('about'),
  close: () => emit('close'),
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
    :profile="profile"
    v-on="listeners"
  >
    <input
      ref="modelUploadInput"
      type="file"
      accept=".zip"
      style="display: none"
      @change="handleModelUploadChange"
    />
  </ManageProjectsView>
</template>
