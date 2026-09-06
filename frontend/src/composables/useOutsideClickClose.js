import { onBeforeUnmount, onMounted, ref } from 'vue'

// An open/closed flag for a panel anchored inside `rootEl`, closed by any
// document click that lands outside that element.
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
