import confetti from 'canvas-confetti'

let timer = null

export function celebrate(duration = 3000) {
  const end = Date.now() + duration

  ;(function frame() {
    confetti({
      particleCount: 2,
      angle: 60,
      spread: 55,
      origin: { x: 0 }
    })

    confetti({
      particleCount: 2,
      angle: 120,
      spread: 55,
      origin: { x: 1 }
    })

    if (Date.now() < end) {
      requestAnimationFrame(frame)
	  setTimeout(() => {
  		stopCelebration()
		}, duration)
	}
  })()
}

function stopCelebration() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}