import { onBeforeUnmount, onMounted, ref } from 'vue'

export function useOutsideClickClose(rootEl) {
  const open = ref(false)

  function toggle() {
    open.value = !open.value
  }

  function close() {
    open.value = false
  }

  function handleDocumentClick(event) {
    if (open.value && !rootEl.value?.contains(event.target)) close()
  }

  onMounted(() => document.addEventListener('click', handleDocumentClick))
  onBeforeUnmount(() => document.removeEventListener('click', handleDocumentClick))

  return { open, toggle, close }
}
