<script setup>
import { ref, watch } from 'vue'
import AppStoreView from './AppStoreView.vue'

const props = defineProps({
  standalone: { type: Boolean, default: true },
  profile: { type: Object, default: null },
  viewStack: { type: Object, default: null }
})

const emit = defineEmits(['close', 'open', 'open-store', 'home', 'profile', 'logout'])

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
    show-logo
    subscribed-only
    show-store-button
    :profile="profile"
    @close="emit('close')"
    @open="emit('open', $event)"
    @open-store="emit('open-store')"
    @home="emit('home')"
    @profile="emit('profile')"
    @logout="emit('logout')"
  />
</template>
