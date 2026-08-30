<script setup>
// The login wall's own screen — a full-viewport overlay, same visual
// register as SplashScreen.vue's 'connecting'/'failed' variants, shown
// whenever authStore.js's needsLogin flips true (see App.vue). Loads
// Google Identity Services on demand rather than unconditionally from
// index.html, so an already-authenticated session never pays for it.
import { onMounted, ref } from 'vue'
import { getAuthProviders, postLogin } from '../api.js'
import { clearLoginRequirement } from '../authStore.js'
import logoUrl from '../assets/avance-logo.png'

const emit = defineEmits(['logged-in'])

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
  let clientId
  try {
    const { providers } = await getAuthProviders()
    clientId = providers.find((p) => p.driver === 'google')?.client_id
  } catch (err) {
    loading.value = false
    error.value = err.message
    return
  }
  if (!clientId) {
    loading.value = false
    error.value = 'Google sign-in is not configured.'
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
    client_id: clientId,
    callback: handleCredentialResponse
  })
  loading.value = false
  // 'filled_blue'/'filled_black' always render the G icon on its own
  // white badge, baked into Google's widget — no official option makes
  // that badge blue, so 'outline' (white button) is the closest match
  // that doesn't clash with it.
  window.google.accounts.id.renderButton(buttonEl.value, {
    theme: 'outline',
    size: 'large',
    shape: 'rectangular',
    text: 'signin_with',
    logo_alignment: 'center',
    width: 280
  })
})
</script>

<template>
  <div class="login-view">
    <div class="login-content">
      <img :src="logoUrl" class="login-logo" alt="Avance" />
      <p v-if="loading" class="login-loading">Loading…</p>
      <div ref="buttonEl" class="login-google-button"></div>
      <p v-if="error" class="login-error">{{ error }}</p>
    </div>
  </div>
</template>

<style scoped>
.login-view {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  /* height, not bottom: 0 (i.e. not inset: 0) — see SplashScreen.vue's
     own .splash for why. */
  height: calc(var(--real-viewport-height, 100vh) + var(--safe-area-bottom));
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--app-base-gradient);
  font-family: system-ui, -apple-system, sans-serif;
  z-index: 1000;
}

.login-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  text-align: center;
  width: 345px;
  box-sizing: border-box;
  padding: 2.5rem 2rem;
  background: white;
  border-radius: 14px;
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.18);
  animation: login-card-in 1s ease-out;
}

@keyframes login-card-in {
  from {
    opacity: 0;
    transform: scale(0.94);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.login-logo {
  width: 150px;
  height: auto;
  margin-top: 0.8rem;
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

/* No radius/overflow clip here on purpose: Google's own ~4px rounding is
   baked into the button graphic itself, not a separate border we can
   isolate — clipping to any other radius cuts into that graphic at the
   corners instead of framing it cleanly. */
.login-google-button {
  min-height: 2.5rem;
  display: inline-block;
  margin-top: 0.6rem;
  transform: scale(0.9);
}

.login-error {
  margin: 0;
  max-width: 320px;
  font-size: 0.9rem;
  color: #c62828;
}
</style>
