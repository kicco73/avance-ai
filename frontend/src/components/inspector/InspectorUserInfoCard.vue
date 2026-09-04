<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'

const props = defineProps({
  user: { type: Object, default: null },
  large: { type: Boolean, default: false },
  // Only ManageUsersView.vue passes true (its own current user is an
  // admin) — the other two Inspector "Info" tabs this card also renders
  // in stay read-only.
  canEditRole: { type: Boolean, default: false }
})

const emit = defineEmits(['change-role'])

const ROLES = ['user', 'customer', 'supervisor', 'admin']

const initial = computed(() => {
  const source = props.user?.name || props.user?.email
  return source ? source.charAt(0).toUpperCase() : '?'
})

function formatDate(iso) {
  return iso ? new Date(iso).toLocaleString() : '—'
}

// Role badge menu — same toggle/click-outside pattern as SettingsMenu.vue.
const roleMenuOpen = ref(false)
const roleMenuRootEl = ref(null)

function toggleRoleMenu() {
  roleMenuOpen.value = !roleMenuOpen.value
}

function selectRole(role) {
  roleMenuOpen.value = false
  if (role !== props.user?.role) emit('change-role', role)
}

function handleClickOutsideRoleMenu(event) {
  if (roleMenuOpen.value && roleMenuRootEl.value && !roleMenuRootEl.value.contains(event.target)) {
    roleMenuOpen.value = false
  }
}

document.addEventListener('click', handleClickOutsideRoleMenu, true)

onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutsideRoleMenu, true)
})
</script>

<template>
  <div class="user-info-card-wrap">
    <p v-if="!user" class="user-info-empty">Select a user to see their profile.</p>

    <div v-else class="user-info-card">
      <img v-if="user.picture_url" :src="user.picture_url" class="user-info-avatar" :class="{ 'user-info-avatar-large': large }" alt="" />
      <div v-else class="user-info-avatar user-info-avatar-fallback" :class="{ 'user-info-avatar-large': large }">{{ initial }}</div>

      <h3 class="user-info-name">{{ user.name ?? user.email }}</h3>
      <p class="user-info-email">{{ user.email }}</p>
      <p v-if="user.provider" class="user-info-provider">{{ user.provider }}</p>
      <span v-if="!canEditRole" class="user-info-role-badge" :class="`user-info-role-${user.role}`">{{ user.role }}</span>
      <div v-else class="user-info-role-menu" ref="roleMenuRootEl">
        <button
          class="user-info-role-badge user-info-role-badge-editable"
          :class="`user-info-role-${user.role}`"
          title="Change role"
          @click="toggleRoleMenu"
        >{{ user.role }}</button>
        <ul v-if="roleMenuOpen" class="user-info-role-menu-list">
          <li v-for="role in ROLES" :key="role">
            <button
              class="user-info-role-menu-item"
              :class="{ 'user-info-role-menu-item-active': role === user.role }"
              @click="selectRole(role)"
            >{{ role }}</button>
          </li>
        </ul>
      </div>

      <div class="user-info-fields">
        <div class="user-info-field">
          <span class="user-info-field-label">User ID</span>
          <span class="user-info-field-value">{{ user.id }}</span>
        </div>
        <div class="user-info-field">
          <span class="user-info-field-label">Member since</span>
          <span class="user-info-field-value">{{ formatDate(user.created_at) }}</span>
        </div>
        <div class="user-info-field">
          <span class="user-info-field-label">Last login</span>
          <span class="user-info-field-value">{{ formatDate(user.last_login) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.user-info-card-wrap { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.user-info-empty { margin: auto; padding: 1rem; font-size: 0.85rem; color: #777; text-align: center; }

.user-info-card { display: flex; flex-direction: column; align-items: center; text-align: center; padding: 0.5rem; overflow-y: auto; }

.user-info-avatar {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  object-fit: cover;
  margin-bottom: 0.75rem;
}

.user-info-avatar-large {
  width: 128px;
  height: 128px;
}

.user-info-avatar-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #4a6fa5;
  color: white;
  font-size: 1.4rem;
  font-weight: 600;
}

.user-info-avatar-fallback.user-info-avatar-large {
  font-size: 2.8rem;
}

.user-info-name { margin: 0 0 0.15rem; font-size: 1.05rem; font-weight: 600; color: #222; }
.user-info-email { margin: 0; font-size: 0.82rem; color: #777; word-break: break-all; }
.user-info-provider { margin: 0.1rem 0 0; font-size: 0.75rem; color: #999; text-transform: capitalize; }

.user-info-role-badge {
  margin-top: 0.5rem;
  padding: 0.15rem 0.6rem;
  border-radius: 999px;
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.user-info-role-user { background: #eee; color: #777; }
.user-info-role-customer { background: #e6f4ea; color: #2e7d32; }
.user-info-role-supervisor { background: #e3edf7; color: #4a6fa5; }
.user-info-role-admin { background: #f7e6d9; color: #a5674a; }

.user-info-role-menu { position: relative; margin-top: 0.5rem; }
.user-info-role-menu .user-info-role-badge { margin-top: 0; }

.user-info-role-badge-editable {
  border: none;
  font: inherit;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  cursor: pointer;
}
.user-info-role-badge-editable:hover { filter: brightness(0.95); }

.user-info-role-menu-list {
  position: absolute;
  top: calc(100% + 0.3rem);
  left: 50%;
  transform: translateX(-50%);
  min-width: 120px;
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  z-index: 100;
  text-align: left;
}
.user-info-role-menu-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.4rem 0.8rem;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.82rem;
  text-transform: capitalize;
  color: #333;
}
.user-info-role-menu-item:hover { background: #f0f4fa; }
.user-info-role-menu-item-active { font-weight: 600; color: #4a6fa5; }

.user-info-fields {
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
  width: 100%;
  margin-top: 1.25rem;
  padding-top: 1rem;
  border-top: 1px solid #eee;
  text-align: left;
}

.user-info-field { display: flex; flex-direction: column; gap: 0.15rem; }
.user-info-field-label { font-size: 0.68rem; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.03em; }
.user-info-field-value { font-size: 0.85rem; color: #333; word-break: break-all; }
</style>
