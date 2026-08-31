<script setup>
// Manage projects' own "Share project" — generates a fresh invite (see
// postCreateInvite, POST /api/projects/{name}/invites) every time this
// dialog opens, then shows a QR code and copyable link for it (see
// shareLink.js/useAppBoot.js for the landing half: ?invite=<code>,
// resolved and activated once the scanning session is authenticated, or
// gated at registration if it isn't one yet).
//
// Pure content only — no backdrop, card chrome, or close button of its
// own: ManageProjectsView.vue opens this through dialogStore.js's
// customDialog(), so DialogHost.vue supplies all of that (including the
// × that closes it) the same way it does for confirm/prompt/choose/
// info/about.
import { onMounted, ref } from 'vue'
import QRCode from 'qrcode'
import { postCreateInvite } from '../../api.js'
import { buildInviteUrl } from '../../shareLink.js'

const props = defineProps({
  projectName: { type: String, required: true },
  uiLabel: { type: String, default: null }
})

const loading = ref(true)
const error = ref('')
const shareUrl = ref('')
const expiresAt = ref(null)
const maxShares = ref(null)
const qrDataUrl = ref(null)
const copied = ref(false)
let copiedTimeout = null

onMounted(async () => {
  try {
    const invite = await postCreateInvite(props.projectName)
    expiresAt.value = invite.expires_at
    maxShares.value = invite.max_shares
    shareUrl.value = buildInviteUrl(invite.code)
    qrDataUrl.value = await QRCode.toDataURL(shareUrl.value, { width: 260, margin: 1 })
  } catch (err) {
    error.value = err.message || 'Could not generate an invite link.'
  } finally {
    loading.value = false
  }
})

async function copyLink() {
  await navigator.clipboard.writeText(shareUrl.value)
  copied.value = true
  clearTimeout(copiedTimeout)
  copiedTimeout = setTimeout(() => { copied.value = false }, 2000)
}
</script>

<template>
  <div class="share-project">
    <h2 class="share-project-title">Share project</h2>
    <p v-if="uiLabel" class="share-project-subtitle">{{ uiLabel }}</p>

    <p v-if="loading" class="share-project-status">Generating invite…</p>
    <p v-else-if="error" class="share-project-status share-project-error">{{ error }}</p>
    <template v-else>
      <div class="share-project-qr-wrap">
        <img v-if="qrDataUrl" :src="qrDataUrl" class="share-project-qr" alt="QR code linking to this project's live chat" />
      </div>
      <p class="share-project-hint">
        Scan to open this project's live chat.
        <template v-if="expiresAt"> Valid until {{ new Date(expiresAt).toLocaleDateString() }}, up to {{ maxShares }} people.</template>
      </p>
      <div class="share-project-link-row">
        <input type="text" class="share-project-link-input" :value="shareUrl" readonly @click="$event.target.select()" />
        <button type="button" class="share-project-copy-btn" @click="copyLink">{{ copied ? 'Copied' : 'Copy' }}</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.share-project {
  text-align: center;
}

.share-project-title {
  margin: 0 0 0.2rem;
  padding-right: 1.6rem; /* clears DialogHost.vue's × close button, top-right */
  font-size: 1.05rem;
  font-weight: 600;
  color: #333;
}

.share-project-subtitle {
  margin: 0 0 0.9rem;
  font-size: 0.85rem;
  color: #777;
}

.share-project-status {
  margin: 1.5rem 0;
  font-size: 0.85rem;
  color: #777;
}

.share-project-error {
  color: #c62828;
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
