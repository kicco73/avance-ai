<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getMe, postEraseData } from '../api.js'
import { confirmDialog, infoDialog } from '../dialogStore.js'
import { disconnect as disconnectChat } from '../chatClient.js'
import { requireLogin } from '../authStore.js'

const emit = defineEmits(['close'])

const profile = ref(null)
const loading = ref(true)
const erasing = ref(false)

const initial = computed(() => {
  const source = profile.value?.name || profile.value?.email
  return source ? source.charAt(0).toUpperCase() : '?'
})

// Same reasoning as ProfileMenu.vue's own imageFailed — picture_url being
// a valid string doesn't mean the image behind it actually loads (an
// expired Google URL, a transient blip, an ad blocker); without this a
// failed load shows as a permanently broken image icon instead of ever
// falling back to the initial-letter avatar.
const imageFailed = ref(false)
watch(() => profile.value?.picture_url, () => {
  imageFailed.value = false
})
const showAvatarImg = computed(() => !!profile.value?.picture_url && !imageFailed.value)

function formatDate(iso) {
  return iso ? new Date(iso).toLocaleString() : '—'
}

async function load() {
  loading.value = true
  try {
    profile.value = await getMe()
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}

onMounted(load)

// Deletes the account and everything tied to it server-side (see
// Db.erase_user_data), then reports success and logs out — the erase
// endpoint already clears the session cookie itself, so this only needs
// to flip the frontend's own state back to the login wall.
async function eraseAllData() {
  const ok = await confirmDialog({
    title: 'Deleting your account',
    body: 'This permanently deletes your account and erases all your data completely. This cannot be undone.',
    okLabel: 'Erase everything',
    danger: true
  })
  if (!ok) return

  erasing.value = true
  try {
    await postEraseData()
  } catch {
    erasing.value = false
    return // already surfaced via apiFetch
  }

  await infoDialog({ title: 'Data erased', body: 'All your data has been erased. Logging you out.' })
  disconnectChat()
  requireLogin()
}
</script>

<template>
  <div class="profile-view-overlay">
    <div class="profile-view-header">
      <button class="back-btn" title="Back" @click="emit('close')">«</button>
      <h2>Profile</h2>
    </div>

    <div class="profile-view-body">
      <p v-if="loading" class="profile-view-status">Loading…</p>

      <div v-else-if="profile" class="profile-card">
        <img v-if="showAvatarImg" :src="profile.picture_url" class="profile-card-avatar" alt="" referrerpolicy="no-referrer" @error="imageFailed = true" />
        <div v-else class="profile-card-avatar profile-card-avatar-fallback">{{ initial }}</div>

        <h3 class="profile-card-name">{{ profile.name ?? profile.email }}</h3>
        <p class="profile-card-email">{{ profile.email }}</p>
        <p v-if="profile.provider" class="profile-card-provider">{{ profile.provider }}</p>

        <div class="profile-card-fields">
          <div class="profile-card-field">
            <span class="profile-card-field-label">Member since</span>
            <span class="profile-card-field-value">{{ formatDate(profile.created_at) }}</span>
          </div>
          <div class="profile-card-field">
            <span class="profile-card-field-label">Last login</span>
            <span class="profile-card-field-value">{{ formatDate(profile.last_login) }}</span>
          </div>
        </div>

        <button type="button" class="erase-data-btn" :disabled="erasing" @click="eraseAllData">
          Delete my account
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.profile-view-overlay {
  position: fixed;
  inset: 0;
  background: #f7f8fa;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.profile-view-header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.75rem 1rem;
  background: white;
  border-bottom: 1px solid #ddd;
}

.profile-view-header h2 {
  margin: 0;
  font-size: 1.1rem;
}

.back-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  padding: 0;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
}

.back-btn:hover {
  background: #4a6fa5;
  color: white;
}

.profile-view-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem 1rem;
}

.profile-view-status {
  color: #666;
  font-size: 0.9rem;
}

.profile-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
  max-width: 420px;
  padding: 2.5rem 2rem;
  background: white;
  border-radius: 16px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
}

.profile-card-avatar {
  width: 110px;
  height: 110px;
  border-radius: 50%;
  object-fit: cover;
  border: 3px solid #4a6fa5;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
}

.profile-card-avatar-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #4a6fa5;
  color: white;
  font-size: 2.4rem;
  font-weight: 600;
}

.profile-card-name {
  margin: 1.25rem 0 0.25rem;
  font-size: 1.5rem;
  font-weight: 600;
  color: #222;
  text-align: center;
}

.profile-card-email {
  margin: 0;
  font-size: 0.9rem;
  color: #777;
}

.profile-card-provider {
  margin: 0.15rem 0 0;
  font-size: 0.8rem;
  color: #999;
  text-transform: capitalize;
}

.profile-card-fields {
  display: flex;
  flex-direction: column;
  gap: 1.1rem;
  width: 100%;
  margin-top: 2rem;
  padding-top: 1.5rem;
  border-top: 1px solid #eee;
}

.profile-card-field {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.profile-card-field-label {
  font-size: 0.72rem;
  font-weight: 600;
  color: #999;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.profile-card-field-value {
  font-size: 0.95rem;
  color: #333;
}

.erase-data-btn {
  margin-top: 2rem;
  padding: 0.55rem 1.2rem;
  border-radius: 8px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
}

.erase-data-btn:hover:not(:disabled) {
  background: #c62828;
  color: white;
}

.erase-data-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
</style>
