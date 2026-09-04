<script setup>
// Topbar avatar menu: same dropdown pattern as SettingsMenu.vue (toggle,
// click-outside-to-close), but the trigger is a circular photo instead
// of an icon, and its two items are Profile/Logout instead of the admin
// actions SettingsMenu.vue owns.
import { computed, onBeforeUnmount, ref, watch } from 'vue'

// App.vue already fetches this once, up front during boot (it needs the
// role before it can even decide which landing view to show) — this just
// renders whatever it's handed rather than fetching its own copy.
const props = defineProps({
  profile: { type: Object, default: null }
})

const emit = defineEmits(['home', 'profile', 'logout'])

const open = ref(false)
const rootEl = ref(null)

const showHome = computed(() => !!props.profile?.role && props.profile.role !== 'user')

const initial = computed(() => {
  const source = props.profile?.name || props.profile?.email
  return source ? source.charAt(0).toUpperCase() : '?'
})

// A Google avatar URL can 404/time out at load time even when
// picture_url itself is a perfectly valid string (an expired token
// behind it, a transient network blip, an ad blocker) — without this,
// that shows as a permanently broken image icon with no fallback ever
// kicking in, since showAvatarImg below only checks the string exists.
// Reset whenever the URL itself changes, so a later successful profile
// reload gets a fresh attempt instead of staying stuck on the old failure.
const imageFailed = ref(false)
watch(() => props.profile?.picture_url, () => {
  imageFailed.value = false
})
const showAvatarImg = computed(() => !!props.profile?.picture_url && !imageFailed.value)

function toggle() {
  open.value = !open.value
}

function selectHome() {
  open.value = false
  emit('home')
}

function selectProfile() {
  open.value = false
  emit('profile')
}

function selectLogout() {
  open.value = false
  emit('logout')
}

function handleClickOutside(event) {
  if (open.value && rootEl.value && !rootEl.value.contains(event.target)) {
    open.value = false
  }
}

document.addEventListener('click', handleClickOutside, true)

onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside, true)
})
</script>

<template>
  <div class="profile-menu" ref="rootEl">
    <button class="profile-btn" :title="profile?.name ?? 'Profile'" @click="toggle">
      <img v-if="showAvatarImg" :src="profile.picture_url" class="profile-avatar-img" alt="" referrerpolicy="no-referrer" @error="imageFailed = true" />
      <span v-else class="profile-avatar-fallback">{{ initial }}</span>
    </button>

    <Transition name="profile-panel">
      <div v-if="open" class="profile-panel">
        <ul class="profile-list">
          <li v-if="showHome">
            <button class="profile-item" @click="selectHome">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                <path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z" />
              </svg>
              <span>Home</span>
            </button>
          </li>
          <li>
            <button class="profile-item" @click="selectProfile">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z" />
              </svg>
              <span>Profile</span>
            </button>
          </li>
          <li>
            <button class="profile-item" @click="selectLogout">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                <path d="M17 7l-1.41 1.41L17.17 10H9v2h8.17l-1.58 1.59L17 15l4-4zM5 5h7V3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h7v-2H5V5z" />
              </svg>
              <span>Logout</span>
            </button>
          </li>
        </ul>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.profile-menu {
  position: relative;
}

.profile-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  padding: 0;
  overflow: hidden;
}

.profile-btn:hover {
  box-shadow: 0 0 0 2px rgba(74, 111, 165, 0.3);
}

.profile-avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.profile-avatar-fallback {
  font-size: 1.2rem;
  font-weight: 600;
}

.profile-panel {
  position: absolute;
  top: calc(100% + 0.4rem);
  right: 0;
  min-width: 160px;
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  z-index: 100;
  overflow: hidden;
  transform-origin: top right;
}

.profile-panel-enter-active,
.profile-panel-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.profile-panel-enter-from,
.profile-panel-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.96);
}

.profile-list {
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
}

.profile-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.9rem;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.9rem;
  color: #4a6fa5;
}

.profile-item svg {
  flex-shrink: 0;
}

.profile-item:hover {
  background: #f0f4fa;
}
</style>
