<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getMe, postEraseData, putWhatsAppPhoneNumber } from '../api.js'
import { confirmDialog, infoDialog, promptDialog } from '../dialogStore.js'
import { disconnect as disconnectChat } from '../chatClient.js'
import { requireLogin } from '../authStore.js'
import AppHeader from './AppHeader.vue'

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

const savingWhatsApp = ref(false)

function validateWhatsAppNumber(value) {
  const trimmed = value.trim()
  if (!trimmed) return ''
  if (!/^\+?\d+$/.test(trimmed)) return 'Digits only (E.164, no spaces or symbols).'
  return ''
}

function describeExistingAccount(outcome) {
  const sessions = outcome.existing_account_session_count
  const history = `${sessions} session${sessions === 1 ? '' : 's'}`
  const created = formatDate(outcome.existing_account_created_at)
  if (outcome.existing_account_provider === 'whatsapp') {
    return `a WhatsApp-only account created on ${created}, with ${history}`
  }
  const provider = outcome.existing_account_provider ?? 'unknown provider'
  return `the account ${outcome.existing_account_id} (${provider}), created on ${created}, with ${history}`
}

async function confirmAccountUnification(outcome) {
  const linkedTo = `This number is already linked to ${describeExistingAccount(outcome)}.`
  if (!outcome.merge_allowed) {
    await infoDialog({
      title: 'Account unification required',
      body: `${linkedTo}\n\nLinking it to your account means unifying the two accounts, `
        + `which requires admin privileges. Ask an administrator.`
    })
    return false
  }
  return confirmDialog({
    title: 'Unify accounts?',
    body: `${linkedTo}\n\nUnifying moves all of its sessions and data to your account `
      + `and removes the other account. This cannot be undone.`,
    okLabel: 'Unify accounts',
    danger: true
  })
}

async function editWhatsAppNumber() {
  const result = await promptDialog({
    title: 'WhatsApp number',
    body: 'The number that chats as your account on WhatsApp. Leave empty to unlink.',
    placeholder: '34600000001',
    initialValue: profile.value?.whatsapp_phone_number ?? '',
    validate: validateWhatsAppNumber
  })
  if (result === null) return
  const number = result.trim() || null
  savingWhatsApp.value = true
  try {
    const outcome = await putWhatsAppPhoneNumber(number)
    if (outcome.merge_required) {
      savingWhatsApp.value = false
      if (!(await confirmAccountUnification(outcome))) return
      savingWhatsApp.value = true
      profile.value = await putWhatsAppPhoneNumber(number, true)
      return
    }
    profile.value = outcome
  } catch {
    // already surfaced via apiFetch
  } finally {
    savingWhatsApp.value = false
  }
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

  await infoDialog({
    title: 'Data erased',
    body: 'All your data has been erased.\nLogging you out.\n\n Thank you for being with us!',
    okLabel: 'Bye!'
  })
  disconnectChat()
  requireLogin()
}
</script>

<template>
  <div class="profile-view-overlay">
    <AppHeader>
      <template #left>
        <button class="app-header-icon-btn" title="Back" @click="emit('close')">«</button>
        <h2 class="app-header-title">Profile</h2>
      </template>
    </AppHeader>

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
          <div class="profile-card-field">
            <span class="profile-card-field-label">WhatsApp</span>
            <div class="profile-card-field-row">
              <span class="profile-card-field-value">{{ profile.whatsapp_phone_number ? `+${profile.whatsapp_phone_number}` : 'Not linked' }}</span>
              <button type="button" class="profile-card-edit-btn" :disabled="savingWhatsApp" @click="editWhatsAppNumber">Edit</button>
            </div>
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
  top: 0;
  left: 0;
  right: 0;
  /* Extends past the viewport's own bottom edge on standalone iOS,
     where WebKit bug #301108 leaves a gap there otherwise — see
     index.html's own viewport meta comment and
     useVisualViewport.js's installViewportOvershoot(). 0px, a no-op,
     everywhere else (a plain browser tab, non-iOS, or once Apple fixes
     the bug). */
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
  /* Side edges only — same split as ManageProjectsView.vue's own
     .manage-projects-overlay (see its comment): top/bottom are reserved
     by .profile-view-header/.profile-view-body instead, the elements
     whose background actually needs to extend behind the notch/home
     indicator rather than showing this fallback color through a gap.
     box-sizing so the padding shrinks the box instead of sitting outside
     it. */
  box-sizing: border-box;
  padding-left: var(--safe-area-left);
  padding-right: var(--safe-area-right);
  background: #f7f8fa;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
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

.profile-card-field-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.profile-card-edit-btn {
  padding: 0.15rem 0.55rem;
  border-radius: 6px;
  border: 1px solid #ddd;
  background: white;
  color: #4a6fa5;
  font-size: 0.78rem;
  cursor: pointer;
}

.profile-card-edit-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
  border-color: #4a6fa5;
}

.profile-card-edit-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
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
