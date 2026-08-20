<script setup>
// The login wall's own screen — a full-viewport overlay, same visual
// register as SplashScreen.vue's 'connecting'/'failed' variants, shown
// whenever authStore.js's needsLogin flips true (see App.vue). Loads
// Google Identity Services on demand rather than unconditionally from
// index.html, so an already-authenticated session never pays for it.
import { onMounted, ref } from 'vue'
import { postLogin } from '../api.js'
import { clearLoginRequirement } from '../authStore.js'

const emit = defineEmits(['logged-in'])

const CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID

const error = ref('')
const loading = ref(true)
const buttonEl = ref(null)

function loadGsiScript() {
  return new Promise((resolve, reject) => {
    if (window.google?.accounts?.id) {
      resolve()
      return
    }
    const script = document.createElement('script')
    script.src = 'https://accounts.google.com/gsi/client'
    script.async = true
    script.defer = true
    script.onload = resolve
    script.onerror = () => reject(new Error('Failed to load Google Identity Services.'))
    document.head.appendChild(script)
  })
}

async function handleCredentialResponse(response) {
  error.value = ''
  try {
    await postLogin('google', response.credential)
    clearLoginRequirement()
    emit('logged-in')
  } catch (err) {
    error.value = err.message || 'Login failed.'
  }
}

onMounted(async () => {
  if (!CLIENT_ID) {
    loading.value = false
    error.value = 'Google sign-in is not configured (missing VITE_GOOGLE_CLIENT_ID).'
    return
  }
  try {
    await loadGsiScript()
  } catch (err) {
    loading.value = false
    error.value = err.message
    return
  }
  window.google.accounts.id.initialize({
    client_id: CLIENT_ID,
    callback: handleCredentialResponse
  })
  loading.value = false
  window.google.accounts.id.renderButton(buttonEl.value, { theme: 'outline', size: 'large' })
})
</script>

<template>
  <div class="login-view">
    <div class="login-content">
      <h1 class="login-title">Avance</h1>
      <p class="login-message">Sign in to continue.</p>
      <p v-if="loading" class="login-loading">Loading…</p>
      <div ref="buttonEl" class="login-google-button"></div>
      <p v-if="error" class="login-error">{{ error }}</p>
    </div>
  </div>
</template>

<style scoped>
.login-view {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: white;
  font-family: system-ui, -apple-system, sans-serif;
  z-index: 1000;
}

.login-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  text-align: center;
  padding: 1.5rem;
}

.login-title {
  margin: 0;
  font-size: 1.3rem;
  font-weight: 600;
  color: #4a6fa5;
  letter-spacing: 0.02em;
}

.login-message {
  margin: 0;
  font-size: 0.9rem;
  color: #777;
}

.login-loading {
  margin: 0;
  font-size: 0.85rem;
  color: #999;
}

.login-google-button {
  min-height: 2.5rem;
}

.login-error {
  margin: 0;
  max-width: 320px;
  font-size: 0.9rem;
  color: #c62828;
}
</style>
