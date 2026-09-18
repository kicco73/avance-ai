<script setup>
import { backgroundAudioUrl } from '../backgroundAudioStore.js'
</script>

<template>
  <audio v-if="backgroundAudioUrl" :key="backgroundAudioUrl" :src="backgroundAudioUrl" autoplay loop class="background-audio-player"></audio>
</template>

<style scoped>
.background-audio-player { display: none; }
</style>
