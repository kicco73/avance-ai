<script setup>
import { inject } from 'vue'

defineProps({
  fixes: { type: Array, required: true }
})

const closeDialog = inject('closeDialog')
</script>

<template>
  <h2 class="fixes-title">index.yml brought up to date</h2>
  <p class="fixes-body">
    This project was written against an older spelling of the format. It now says the same thing
    the way today's format states it, and the change is saved:
  </p>
  <ul class="fixes-list">
    <li v-for="fix in fixes" :key="fix" class="fixes-item">{{ fix }}</li>
  </ul>
  <div class="fixes-actions">
    <button class="fixes-ok-btn" @click="closeDialog(null)">OK</button>
  </div>
</template>

<style scoped>
.fixes-title {
  margin: 0 0 0.6rem;
  padding-right: 1.6rem;
  font-size: 1.05rem;
  font-weight: 600;
  color: #333;
}

.fixes-body {
  margin: 0 0 1rem;
  font-size: 0.9rem;
  color: #333;
}

.fixes-list {
  max-height: 40vh;
  overflow-y: auto;
  margin: 0 0 1rem;
  padding: 0.4rem 0.6rem;
  list-style: none;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  background: #fafafa;
}

.fixes-item {
  padding: 0.25rem 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.8rem;
  color: #333;
  overflow-wrap: anywhere;
}

.fixes-item + .fixes-item {
  border-top: 1px solid #ececec;
}

.fixes-actions {
  display: flex;
  justify-content: flex-end;
}

.fixes-ok-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: white;
  cursor: pointer;
  font-size: 0.85rem;
}
</style>
