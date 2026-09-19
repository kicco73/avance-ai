import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createApp } from 'vue'

import ChartDialog from '../src/components/chat/ChartDialog.vue'

const SERIES = [
  { line: 'Emotional exhaustion', value: 12 },
  { line: 'Overall', value: 42 }
]

describe('the chart dialog', () => {
  let container
  let app

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    app?.unmount()
    container.remove()
  })

  function widths(props) {
    app = createApp(ChartDialog, { title: 'Score', series: SERIES, ...props })
    app.mount(container)
    return [...container.querySelectorAll('.chart-bar-fill')].map((bar) => bar.style.width)
  }

  it('measures every bar against max_scale when it is given, so a low score reads as a short bar', () => {
    expect(widths({ maxScale: 100 })).toEqual(['12%', '42%'])
  })

  it('spreads the bars across their own values when no max_scale is given', () => {
    expect(widths({})).toEqual(['0%', '100%'])
  })

  it('never draws past the end of the track', () => {
    expect(widths({ maxScale: 10 })).toEqual(['100%', '100%'])
  })
})
