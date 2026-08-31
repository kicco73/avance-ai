<script setup>
// Shown instead of TermsView.vue for a session that authenticated but
// has no User row yet (see App.vue's own pingBackend — a 403 off GET
// /api/state) AND arrived with no "share project" invite link (see
// shareLink.js's peekInviteCode — App.vue picks between this and
// TermsView based on it). Self-registration is invite-only now
// (AuthService.complete_registration refuses an invite code that
// doesn't clear its exists/not-expired/under-max-shares check), so a
// plain Google sign-in with no invite has nothing to accept here — just
// a way back out.
//
// Deliberately its own component rather than a mode/flag on TermsView:
// this isn't a consent screen (nothing to Accept), and TermsView is
// still reused as-is by LiveChatWindow.vue for an unrelated concept
// (a project's own legal/terms.md) that this gate must never affect.
import logoUrl from '../assets/avance-logo.png'

const emit = defineEmits(['logout'])
</script>

<template>
  <div class="invite-required">
    <div class="invite-required-content">
      <img :src="logoUrl" class="invite-required-logo" alt="Avance" />
      <h1 class="invite-required-title">Registration required</h1>
      <p class="invite-required-message">
        This platform is invite-only — self sign-up isn't available. Ask whoever invited you for a share link,
        then sign in again from that link.
      </p>
      <button type="button" class="invite-required-logout" @click="emit('logout')">Log out</button>
    </div>
  </div>
</template>

<style scoped>
.invite-required {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  /* Same standalone-iOS bottom-overshoot convention as every other
     full-viewport screen — see App.vue's .app-backdrop comment for why. */
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--app-base-gradient);
  font-family: system-ui, -apple-system, sans-serif;
  z-index: 1000;
  padding: calc(2rem + var(--safe-area-top)) calc(2rem + var(--safe-area-right))
    calc(2rem + var(--safe-area-bottom)) calc(2rem + var(--safe-area-left));
  box-sizing: border-box;
}

.invite-required-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.9rem;
  text-align: center;
  width: 360px;
  max-width: 100%;
  box-sizing: border-box;
  padding: 2.5rem 2rem;
  background: white;
  border-radius: 14px;
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.18);
  animation: invite-required-in 0.35s ease-out;
}

@keyframes invite-required-in {
  from {
    opacity: 0;
    transform: scale(0.96);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.invite-required-logo {
  width: 130px;
  height: auto;
}

.invite-required-title {
  margin: 0;
  font-size: 1.15rem;
  font-weight: 600;
  color: #4a6fa5;
}

.invite-required-message {
  margin: 0;
  font-size: 0.88rem;
  line-height: 1.5;
  color: #555;
}

.invite-required-logout {
  margin-top: 0.4rem;
  padding: 0.55rem 1.4rem;
  border-radius: 8px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  font-size: 0.9rem;
  font-weight: 600;
  cursor: pointer;
}

.invite-required-logout:hover {
  background: #4a6fa5;
  color: white;
}
</style>
