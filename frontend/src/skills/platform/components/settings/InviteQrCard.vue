<script setup>
import { ref } from 'vue'

const props = defineProps({
  qrDataUrl: { type: String, default: null },
  linkUrl: { type: String, required: true },
  hint: { type: String, default: '' }
})

const copied = ref(false)
let copiedTimeout = null

async function copyLink() {
  await navigator.clipboard.writeText(props.linkUrl)
  copied.value = true
  clearTimeout(copiedTimeout)
  copiedTimeout = setTimeout(() => { copied.value = false }, 2000)
}
</script>

<template>
  <div class="invite-qr-card">
    <div class="invite-qr-wrap">
      <img v-if="qrDataUrl" :src="qrDataUrl" class="invite-qr-img" alt="QR code" />
    </div>
    <p v-if="hint" class="invite-qr-hint">{{ hint }}</p>
    <div class="invite-qr-link-row">
      <a :href="linkUrl" target="_blank" rel="noopener noreferrer" class="invite-qr-link-input">{{ linkUrl }}</a>
      <button type="button" class="invite-qr-copy-btn" @click="copyLink">{{ copied ? 'Copied' : 'Copy' }}</button>
    </div>
  </div>
</template>

<style scoped>
.invite-qr-card {
  text-align: center;
}

.invite-qr-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 260px;
  height: 260px;
  margin: 0 auto;
  border-radius: 8px;
  background: #fafafa;
  border: 1px solid #eee;
}

.invite-qr-img {
  display: block;
  width: 260px;
  height: 260px;
}

.invite-qr-hint {
  margin: 0.8rem 0 0;
  font-size: 0.8rem;
  color: #777;
}

.invite-qr-link-row {
  display: flex;
  gap: 0.4rem;
  margin-top: 0.8rem;
}

.invite-qr-link-input {
  display: block;
  flex: 1;
  min-width: 0;
  padding: 0.4rem 0.55rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  font-size: 0.78rem;
  color: #4a6fa5;
  background: #fafafa;
  text-align: left;
  text-decoration: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.invite-qr-link-input:hover {
  text-decoration: underline;
}

.invite-qr-copy-btn {
  flex-shrink: 0;
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: #4a6fa5;
  color: white;
  font-size: 0.82rem;
  cursor: pointer;
}

.invite-qr-copy-btn:hover {
  background: #3d5c8a;
}
</style>
