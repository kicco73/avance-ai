<script setup>
// Manage projects' own "Share project" — generates a fresh invite (see
// postCreateInvite, POST /api/projects/{project_id}/invites) every time this
// dialog opens, then shows a QR code and copyable link for it: a Web tab
// (see shareLink.js/useAppBoot.js for the landing half: ?invite=<code>,
// resolved and activated once the scanning session is authenticated, or
// gated at registration if it isn't one yet) and, when whatsapp-service
// is configured, a WhatsApp tab pointing at the same invite code (see
// WhatsAppService._handle_unlinked, the receiving half). Both tabs share
// the same QR/link/copy layout (InviteQrCard.vue).
//
// Pure content only — no backdrop, card chrome, or close button of its
// own: ManageProjectsView.vue opens this through dialogStore.js's
// customDialog(), so DialogHost.vue supplies all of that (including the
// × that closes it) the same way it does for confirm/prompt/choose/
// info/about.
import { computed, onMounted, ref } from 'vue'
import QRCode from 'qrcode'
import { postCreateInvite } from '../../api.js'
import { buildInviteUrl } from '../../shareLink.js'
import InviteQrCard from './InviteQrCard.vue'

const props = defineProps({
  projectId: { type: String, required: true },
  uiLabel: { type: String, default: null }
})

const loading = ref(true)
const error = ref('')
const channel = ref('web')
const webUrl = ref('')
const webQr = ref(null)
const whatsappUrl = ref('')
const whatsappQr = ref(null)
const expiresAt = ref(null)
const maxShares = ref(null)

const hasWhatsapp = computed(() => !!whatsappUrl.value)

onMounted(async () => {
  try {
    const invite = await postCreateInvite(props.projectId)
    expiresAt.value = invite.expires_at
    maxShares.value = invite.max_shares
    webUrl.value = buildInviteUrl(invite.code)
    webQr.value = await QRCode.toDataURL(webUrl.value, { width: 260, margin: 1 })
    if (invite.whatsapp_url) {
      whatsappUrl.value = invite.whatsapp_url
      whatsappQr.value = await QRCode.toDataURL(whatsappUrl.value, { width: 260, margin: 1 })
    }
  } catch (err) {
    error.value = err.message || 'Could not generate an invite link.'
  } finally {
    loading.value = false
  }
})

const hint = computed(() => {
  const base = channel.value === 'web'
    ? "Scan to open this project's live chat."
    : 'Scan to open WhatsApp with this invite ready to send.'
  return expiresAt.value
    ? `${base} Valid until ${new Date(expiresAt.value).toLocaleDateString()}, up to ${maxShares.value} people.`
    : base
})
</script>

<template>
  <div class="share-project">
    <h2 class="share-project-title">Share project</h2>
    <p v-if="uiLabel" class="share-project-subtitle">{{ uiLabel }}</p>

    <p v-if="loading" class="share-project-status">Generating invite…</p>
    <p v-else-if="error" class="share-project-status share-project-error">{{ error }}</p>
    <template v-else>
      <div v-if="hasWhatsapp" class="share-project-segmented">
        <button
          type="button"
          class="share-project-segment-btn"
          :class="{ 'share-project-segment-active': channel === 'web' }"
          @click="channel = 'web'"
        >Web</button>
        <button
          type="button"
          class="share-project-segment-btn"
          :class="{ 'share-project-segment-active': channel === 'whatsapp' }"
          @click="channel = 'whatsapp'"
        >WhatsApp</button>
      </div>
      <InviteQrCard
        :qr-data-url="channel === 'web' ? webQr : whatsappQr"
        :link-url="channel === 'web' ? webUrl : whatsappUrl"
        :hint="hint"
      />
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

.share-project-segmented {
  display: inline-flex;
  margin: 0 0 1rem;
  padding: 0.2rem;
  border-radius: 8px;
  background: #f0f0f2;
}

.share-project-segment-btn {
  padding: 0.35rem 1rem;
  border: none;
  border-radius: 6px;
  background: none;
  color: #666;
  font-size: 0.82rem;
  cursor: pointer;
}

.share-project-segment-active {
  background: white;
  color: #2c4d7a;
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15);
}
</style>
