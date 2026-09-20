import confetti from 'canvas-confetti'

let timer = null

function scopedConfetti(scopeEl) {
  const canvas = document.createElement('canvas')
  canvas.style.position = 'absolute'
  canvas.style.inset = '0'
  canvas.style.width = '100%'
  canvas.style.height = '100%'
  canvas.style.pointerEvents = 'none'
  scopeEl.appendChild(canvas)
  return { fire: confetti.create(canvas, { resize: true }), canvas }
}

export function celebrate(duration = 3000, scopeEl = null) {
  const end = Date.now() + duration
  const scoped = scopeEl ? scopedConfetti(scopeEl) : null
  const fire = scoped ? scoped.fire : confetti

  ;(function frame() {
    fire({
      particleCount: 2,
      angle: 60,
      spread: 55,
      origin: { x: 0 }
    })

    fire({
      particleCount: 2,
      angle: 120,
      spread: 55,
      origin: { x: 1 }
    })

    if (Date.now() < end) {
      requestAnimationFrame(frame)
	  setTimeout(() => {
  		stopCelebration(scoped)
		}, duration)
	}
  })()
}

function stopCelebration(scoped) {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
  scoped?.canvas.remove()
}