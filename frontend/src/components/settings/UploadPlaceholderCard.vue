<script setup>
import { computed, ref, watch } from 'vue'
import '../../styles/projectCard.css'
import { getProjectFiles, projectFileContentUrl } from '../../api.js'
import { findIconFile } from '../../projectIcon.js'
import avanceLogoUrl from '../../assets/avance-logo.png'

const props = defineProps({
  uploadProgress: { type: Number, default: null },
  uploadProjectId: { type: String, default: null },
  uploadIconReady: { type: Boolean, default: false }
})

const title = computed(() => (props.uploadProgress == null ? 'Uploading' : 'Installing'))

const iconFailed = ref(false)
const iconLoaded = ref(false)
const iconFile = ref(null)

watch(() => props.uploadIconReady, async (ready) => {
  if (!ready || !props.uploadProjectId) return
  let found = null
  try {
    const { files } = await getProjectFiles(props.uploadProjectId)
    found = findIconFile(files)
  } catch {
    return
  }
  if (!found) return
  iconFile.value = found
  const preload = new Image()
  preload.onload = () => { iconLoaded.value = true }
  preload.onerror = () => { iconFailed.value = true }
  preload.src = projectFileContentUrl(props.uploadProjectId, found)
})
</script>

<template>
  <div class="project-card upload-placeholder">
    <span class="upload-icon-wrap">
      <img :src="avanceLogoUrl" class="project-card-fallback-logo upload-icon-glow" alt="" />
      <Transition name="upload-icon">
        <img
          v-if="iconLoaded && !iconFailed"
          :src="projectFileContentUrl(uploadProjectId, iconFile)"
          class="project-card-icon upload-icon-glow"
          alt=""
        />
      </Transition>
    </span>
    <span class="project-card-body">
      <span class="project-card-title-row">
        <span class="project-card-title">{{ title }}</span>
      </span>
      <span class="upload-bar-track">
        <span class="upload-bar-fill" :style="{ width: `${uploadProgress ?? 0}%` }"></span>
      </span>
    </span>
  </div>
</template>

<style scoped>
.upload-placeholder {
  cursor: default;
  pointer-events: none;
}

.upload-icon-wrap {
  position: relative;
  flex-shrink: 0;
  width: 65px;
  height: 65px;
}

.upload-icon-wrap .project-card-fallback-logo,
.upload-icon-wrap .project-card-icon {
  position: absolute;
  inset: 0;
}

.upload-icon-enter-active {
  transition: opacity 0.4s ease;
}

.upload-icon-enter-from {
  opacity: 0;
}

.upload-icon-glow {
  animation: upload-icon-glow-pulse 1.8s ease-in-out infinite;
}

@keyframes upload-icon-glow-pulse {
  0%, 100% {
    filter: drop-shadow(0 0 2px rgba(74, 111, 165, 0.35));
  }
  50% {
    filter: drop-shadow(0 0 10px rgba(74, 111, 165, 0.9));
  }
}

.upload-bar-track {
  display: block;
  width: 100%;
  margin-top: 0.3rem;
  height: 8px;
  border-radius: 999px;
  background: #eee;
  overflow: hidden;
}

.upload-bar-fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: #4a6fa5;
  transition: width 0.3s ease;
}
</style>
