// Chat's own side of the flip (see ManageProjectsView's .view-flip-base*
// classes for the other side), driven by JS hooks instead of Vue's
// CSS-class Transition convention — see the long comment above
// .view-flip-base in <style> for why: a Transition whose :name changes in
// the same tick as its child's v-if doesn't reliably re-resolve its
// enter/leave classes on an already-mounted child, so a CSS-class
// approach kept leaving chat with stale "forward" values on a 'back' pop.
// Reading navDirection.value directly inside these hooks, called by Vue
// at the actual moment each transition starts, sidesteps that entirely.
// Chat only ever *enters* forward (its one entry point, "Open chat" on a
// Manage projects row, always pushes) but can *leave* either way (a pop
// back to Manage projects, or a further forward push to Edit/Label/Manage
// users while chat was showing) — see the two branches in onChatLeave.
//
// This file was tuned against real rendering (see
// [[feedback_js_hook_transitions_for_dynamic_direction]] in project
// memory) — verify any change here visually in a real browser, not just
// by reading the code or running its tests.
export function useChatFlipTransition(navDirection) {
  // Reads the live --flip-duration custom property (see .app-body's own
  // CSS) rather than duplicating its value here, so the debug-speed knob
  // stays a single source of truth.
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
        // Back to resting on the CSS class's own z-index:100 — the inline
        // 101 above only needed to hold during the transition itself.
        el.style.zIndex = ''
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
