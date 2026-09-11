<script setup>
// Manage projects' own "Share project" — generates a fresh invite (see
// postCreateInvite, POST /api/skills/platform/projects/{project_id}/invites) every time this
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
import { shareChannels } from '../../../registry.js'
import { postCreateInvite } from '../../api.js'
import { buildInviteUrl } from '../../../../shareLink.js'
import InviteQrCard from './InviteQrCard.vue'

const props = defineProps({
  projectId: { type: String, required: true },
  uiLabel: { type: String, default: null }
})

const loading = ref(true)
const error = ref('')
// The web link is this dialog's own; any other way of handing an invite
// over is contributed by whoever owns that channel, and names the field
// of the invite its link arrives in.
const WEB_CHANNEL = { id: 'web', label: 'Web', hint: "Scan to open this project's live chat." }

const channel = ref(WEB_CHANNEL.id)
const links = ref({})
const qrs = ref({})
const expiresAt = ref(null)
const maxShares = ref(null)

const channels = computed(() => [WEB_CHANNEL, ...shareChannels.value].filter((entry) => links.value[entry.id]))
const activeChannel = computed(() => channels.value.find((entry) => entry.id === channel.value) ?? WEB_CHANNEL)

onMounted(async () => {
  try {
    const invite = await postCreateInvite(props.projectId)
    expiresAt.value = invite.expires_at
    maxShares.value = invite.max_shares
    links.value = { [WEB_CHANNEL.id]: buildInviteUrl(invite.code) }
    for (const entry of shareChannels.value) {
      if (invite[entry.inviteField]) links.value[entry.id] = invite[entry.inviteField]
    }
    for (const [id, url] of Object.entries(links.value)) {
      qrs.value[id] = await QRCode.toDataURL(url, { width: 260, margin: 1 })
    }
  } catch (err) {
    error.value = err.message || 'Could not generate an invite link.'
  } finally {
    loading.value = false
  }
})

const hint = computed(() => {
  const base = activeChannel.value.hint
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
      <div v-if="channels.length > 1" class="share-project-segmented">
        <button
          v-for="entry in channels"
          :key="entry.id"
          type="button"
          class="share-project-segment-btn"
          :class="{ 'share-project-segment-active': channel === entry.id }"
          @click="channel = entry.id"
        >{{ entry.label }}</button>
      </div>
      <InviteQrCard
        :qr-data-url="qrs[activeChannel.id]"
        :link-url="links[activeChannel.id]"
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
