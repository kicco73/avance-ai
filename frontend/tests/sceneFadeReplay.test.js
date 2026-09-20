import { describe, expect, it } from 'vitest'
import { SceneFade } from '../src/components/chat/sceneFade.js'

function fakeAnimation() {
  return {
    currentTime: 120,
    playCount: 0,
    play() { this.playCount++ }
  }
}

function elementRunning(animations) {
  return { getAnimations: () => animations }
}

describe('SceneFade replays the scene animation the stylesheet declares', () => {
  it('rewinds and plays every animation running on the shell', () => {
    const first = fakeAnimation()
    const second = fakeAnimation()
    const fade = new SceneFade()
    fade.attach(elementRunning([first, second]))

    fade.replay()

    expect(first.currentTime).toBe(0)
    expect(first.playCount).toBe(1)
    expect(second.currentTime).toBe(0)
    expect(second.playCount).toBe(1)
  })

  it('replays again on every call, so a second state change fades again', () => {
    const animation = fakeAnimation()
    const fade = new SceneFade()
    fade.attach(elementRunning([animation]))

    fade.replay()
    animation.currentTime = 200
    fade.replay()

    expect(animation.currentTime).toBe(0)
    expect(animation.playCount).toBe(2)
  })

  it('does nothing once detached, and nothing where the engine has no animations', () => {
    const animation = fakeAnimation()
    const fade = new SceneFade()
    fade.attach(elementRunning([animation]))
    fade.detach()

    expect(() => fade.replay()).not.toThrow()
    expect(animation.currentTime).toBe(120)

    fade.attach({})
    expect(() => fade.replay()).not.toThrow()
  })
})
