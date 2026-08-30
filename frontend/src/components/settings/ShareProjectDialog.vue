<script setup>
// Manage projects' own "Share project" — a QR code (and the plain link
// it encodes) for projectId's share URL (see shareLink.js/useAppBoot.js
// for the landing half: ?project=<id>, resolved and activated once the
// scanning session is authenticated). Rendered directly by
// ManageProjectsView.vue as a standalone overlay rather than through
// dialogStore.js/DialogHost.vue — its QR image and copyable link don't
// fit that store's fixed confirm/prompt/choose/info/about kinds.
import { onMounted, ref } from 'vue'
import QRCode from 'qrcode'
import { buildShareUrl } from '../../shareLink.js'

const props = defineProps({
  projectId: { type: String, required: true },
  uiLabel: { type: String, default: null }
})

const emit = defineEmits(['close'])

const shareUrl = buildShareUrl(props.projectId)
const qrDataUrl = ref(null)
const copied = ref(false)
let copiedTimeout = null

onMounted(async () => {
  qrDataUrl.value = await QRCode.toDataURL(shareUrl, { width: 260, margin: 1 })
})

async function copyLink() {
  await navigator.clipboard.writeText(shareUrl)
  copied.value = true
  clearTimeout(copiedTimeout)
  copiedTimeout = setTimeout(() => { copied.value = false }, 2000)
}

function onBackdropClick(event) {
  if (event.target === event.currentTarget) emit('close')
}
</script>

<template>
  <div class="share-project-overlay" @click="onBackdropClick">
    <div class="share-project-card">
      <button type="button" class="share-project-close" title="Close" @click="emit('close')">×</button>
      <h2 class="share-project-title">Share project</h2>
      <p v-if="uiLabel" class="share-project-subtitle">{{ uiLabel }}</p>
      <div class="share-project-qr-wrap">
        <img v-if="qrDataUrl" :src="qrDataUrl" class="share-project-qr" alt="QR code linking to this project's live chat" />
      </div>
      <p class="share-project-hint">Scan to open this project's live chat.</p>
      <div class="share-project-link-row">
        <input type="text" class="share-project-link-input" :value="shareUrl" readonly @click="$event.target.select()" />
        <button type="button" class="share-project-copy-btn" @click="copyLink">{{ copied ? 'Copied' : 'Copy' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.share-project-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
  background: rgba(0, 0, 0, 0.35);
}

.share-project-card {
  position: relative;
  width: 100%;
  max-width: 320px;
  box-sizing: border-box;
  padding: 1.4rem;
  border-radius: 10px;
  background: white;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
  text-align: center;
}

.share-project-close {
  position: absolute;
  top: 0.6rem;
  right: 0.6rem;
  width: 1.8rem;
  height: 1.8rem;
  border: none;
  border-radius: 6px;
  background: none;
  color: #777;
  font-size: 1.3rem;
  line-height: 1;
  cursor: pointer;
}

.share-project-close:hover {
  background: #f0f0f0;
}

.share-project-title {
  margin: 0 0 0.2rem;
  font-size: 1.05rem;
  font-weight: 600;
  color: #333;
}

.share-project-subtitle {
  margin: 0 0 0.9rem;
  font-size: 0.85rem;
  color: #777;
}

.share-project-qr-wrap {
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

.share-project-qr {
  display: block;
  width: 260px;
  height: 260px;
}

.share-project-hint {
  margin: 0.8rem 0 0;
  font-size: 0.8rem;
  color: #777;
}

.share-project-link-row {
  display: flex;
  gap: 0.4rem;
  margin-top: 0.8rem;
}

.share-project-link-input {
  flex: 1;
  min-width: 0;
  padding: 0.4rem 0.55rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  font-size: 0.78rem;
  color: #444;
  background: #fafafa;
}

.share-project-copy-btn {
  flex-shrink: 0;
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: #4a6fa5;
  color: white;
  font-size: 0.82rem;
  cursor: pointer;
}

.share-project-copy-btn:hover {
  background: #3d5c8a;
}
</style>
