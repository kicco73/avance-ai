<script setup>
import { ref, watch } from 'vue'
import AppStoreView from './AppStoreView.vue'

const props = defineProps({
  standalone: { type: Boolean, default: true },
  currentUserRole: { type: String, default: null },
  profile: { type: Object, default: null },
  viewStack: { type: Object, default: null }
})

const emit = defineEmits(['close', 'open', 'open-store', 'manage-projects', 'manage-users', 'manage-services', 'home', 'profile', 'logout'])

const appStoreViewRef = ref(null)

watch(() => props.viewStack?.pushedView.value, (now, before) => {
  if (before === 'appStore' && now == null) appStoreViewRef.value?.refresh()
})

defineExpose({ refresh: () => appStoreViewRef.value?.refresh() })
</script>

<template>
  <AppStoreView
    ref="appStoreViewRef"
    :standalone="standalone"
    :role="currentUserRole"
    show-logo
    subscribed-only
    show-store-button
    :profile="profile"
    @close="emit('close')"
    @open="emit('open', $event)"
    @open-store="emit('open-store')"
    @manage-projects="emit('manage-projects')"
    @manage-users="emit('manage-users')"
    @manage-services="emit('manage-services')"
    @home="emit('home')"
    @profile="emit('profile')"
    @logout="emit('logout')"
  />
</template>
