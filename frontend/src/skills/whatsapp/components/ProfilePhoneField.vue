<script setup>
import { ref } from 'vue'
import { putUserPhoneNumber } from '../../../api.js'
import { confirmDialog, promptDialog } from '../../../dialogStore.js'

const props = defineProps({
  profile: { type: Object, required: true }
})

const emit = defineEmits(['updated'])

const saving = ref(false)

function validateNumber(value) {
  const digits = (value ?? '').replace(/[\s-]/g, '')
  if (!digits) return ''
  if (!/^\+?\d{6,15}$/.test(digits)) return 'Enter a phone number in international format, e.g. +39 333 1234567.'
  return ''
}

function describeExisting(outcome) {
  const created = new Date(outcome.existing_account_created_at).toLocaleDateString()
  const history = `${outcome.existing_account_sessions} conversation${outcome.existing_account_sessions === 1 ? '' : 's'}`
  if (outcome.existing_account_provider === 'whatsapp') {
    return `a WhatsApp-only account created on ${created}, with ${history}`
  }
  return `an account created on ${created}, with ${history}`
}

async function edit() {
  const number = await promptDialog({
    title: 'WhatsApp number',
    body: 'The number that chats as your account on WhatsApp. Leave empty to unlink.',
    okLabel: 'Save',
    initialValue: props.profile?.whatsapp_phone_number ?? '',
    validate: validateNumber
  })
  if (number === null) return
  saving.value = true
  try {
    const outcome = await putUserPhoneNumber(number)
    if (outcome.conflict) {
      saving.value = false
      const merge = await confirmDialog({
        title: 'Number already in use',
        body: `That number already belongs to ${describeExisting(outcome)}. Merge it into this account?`,
        okLabel: 'Merge',
        danger: true
      })
      if (!merge) return
      saving.value = true
      emit('updated', await putUserPhoneNumber(number, true))
      return
    }
    emit('updated', outcome)
  } catch {
    // already surfaced via apiFetch
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="profile-card-field">
    <span class="profile-card-field-label">WhatsApp</span>
    <div class="profile-card-field-row">
      <span class="profile-card-field-value">{{ profile.whatsapp_phone_number ? `+${profile.whatsapp_phone_number}` : 'Not linked' }}</span>
      <button type="button" class="profile-card-edit-btn" :disabled="saving" @click="edit">Edit</button>
    </div>
  </div>
</template>
