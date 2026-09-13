export function useChatFlipTransition(navDirection) {
  function flipDurationMs() {
    const raw = getComputedStyle(document.querySelector('.app-body')).getPropertyValue('--flip-duration').trim()
    const value = parseFloat(raw)
    if (!value) return 500
    return raw.endsWith('ms') ? value : value * 1000
  }

  function afterTransform(el, done) {
    const onEnd = (event) => {
      if (event.propertyName !== 'transform' || event.target !== el) return
      el.removeEventListener('transitionend', onEnd)
      done()
    }
    el.addEventListener('transitionend', onEnd)
  }

  function onChatBeforeEnter(el) {
    el.style.transition = 'none'
    el.style.backfaceVisibility = 'hidden'
    el.style.zIndex = '101'
    el.style.transform = 'rotateY(-90deg)'
  }

  function onChatEnter(el, done) {
    const duration = flipDurationMs()
    setTimeout(() => {
      el.style.transition = `transform ${duration}ms ease-out`
      requestAnimationFrame(() => {
        el.style.transform = 'rotateY(0deg)'
      })
      afterTransform(el, () => {
        el.style.zIndex = ''
        el.style.transform = ''
        el.style.transition = ''
        el.style.backfaceVisibility = ''
        done()
      })
    }, duration)
  }

  function onChatBeforeLeave(el) {
    el.style.backfaceVisibility = 'hidden'
    el.style.zIndex = navDirection.value === 'back' ? '101' : ''
    el.style.transform = 'rotateY(0deg)'
  }

  function onChatLeave(el, done) {
    const duration = flipDurationMs()
    const isBack = navDirection.value === 'back'
    el.style.transition = `transform ${duration}ms ${isBack ? 'ease-in' : 'ease-in-out'}`
    requestAnimationFrame(() => {
      el.style.transform = `rotateY(${isBack ? -90 : 90}deg)`
    })
    afterTransform(el, done)
  }

  return { onChatBeforeEnter, onChatEnter, onChatBeforeLeave, onChatLeave }
}
