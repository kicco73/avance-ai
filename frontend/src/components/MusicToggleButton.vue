<script setup>
import { backgroundAudioUrl, backgroundAudioPlaying, toggleBackgroundAudio } from '../backgroundAudioStore.js'
</script>

<template>
  <button
    v-if="backgroundAudioUrl"
    type="button"
    class="app-header-icon-btn"
    :title="backgroundAudioPlaying ? 'Mute music' : 'Play music'"
    @click="toggleBackgroundAudio"
  >
    <svg v-if="backgroundAudioPlaying" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <rect x="7" y="4" width="4" height="16" rx="1" />
      <rect x="13" y="4" width="4" height="16" rx="1" />
    </svg>
    <svg v-else viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <path d="M9 4a1 1 0 0 0-1 1v14a1 1 0 0 0 1.53.848l11-7a1 1 0 0 0 0-1.696l-11-7A1 1 0 0 0 9 4z" />
    </svg>
  </button>
</template>
