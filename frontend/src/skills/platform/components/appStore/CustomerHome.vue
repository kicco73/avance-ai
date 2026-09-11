<script setup>
import { ref, watch } from 'vue'
import AppStoreView from './AppStoreView.vue'

const props = defineProps({
  standalone: { type: Boolean, default: true },
  profile: { type: Object, default: null },
  // The navigation stack (see composables/useViewStack.js), so this home
  // can notice its own overlay closing. It used to hand a ref upward for
  // popPushedView to call, which made the core name a view it does not
  // own — and left the rule "the core never names a skill" with an
  // exception nobody could remove.
  viewStack: { type: Object, default: null }
})

const emit = defineEmits(['close', 'open', 'open-store', 'home', 'profile', 'logout'])

const appStoreViewRef = ref(null)

// Coming back from the store: what was bought while it was open changes
// what belongs on this screen.
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
