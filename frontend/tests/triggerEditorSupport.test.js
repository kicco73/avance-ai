import { describe, expect, it } from 'vitest'
import { EditorState } from '@codemirror/state'
import { CompletionContext } from '@codemirror/autocomplete'
import { completeIdentifiers, completionInfo, isProxyNamespace, namespaceOf, NAMESPACE_COLORS } from '../src/triggerEditorSupport.js'

const REGISTRY = {
  signal: { mood: 'How positive the user sounds.' },
  env: { visits: '' },
  system: { today: "Today's date.", time: 'The current time.' },
  session: { number_of_user_sessions: 'How many sessions.' },
  'session.metric': { engagement: 'Engagement score.', state_stability: 'State stability score.' },
  metric: { retention: 'Retention score.', activity_consistency: 'Activity consistency score.' }
}

function contextAt(text, explicit = false) {
  const state = EditorState.create({ doc: text })
  return new CompletionContext(state, text.length, explicit)
}

describe('completeIdentifiers', () => {
  it('suggests every top-level namespace for a bare partial word, unfiltered — CodeMirror\'s own autocomplete does the actual text matching downstream (see @codemirror/autocomplete\'s own CompletionResult.options docs)', () => {
    const result = completeIdentifiers(contextAt('sig'), REGISTRY)

    expect(new Set(result.options.map((o) => o.label))).toEqual(
      new Set(['signal', 'env', 'system', 'session', 'metric'])
    )
    const signalOption = result.options.find((o) => o.label === 'signal')
    expect(signalOption.type).toBe('namespace')
    // No trailing "." — completing a namespace name shouldn't force the
    // user into typing on that field before they can look elsewhere.
    expect(signalOption.apply).toBe('signal')
  })

  it('suggests every top-level namespace on an explicit empty request', () => {
    const result = completeIdentifiers(contextAt('', true), REGISTRY)

    expect(new Set(result.options.map((o) => o.label))).toEqual(
      new Set(['signal', 'env', 'system', 'session', 'metric'])
    )
    // "session.metric" is a dotted registry key, never itself a bare
    // namespace word a user would type directly.
    expect(result.options.some((o) => o.label === 'session.metric')).toBe(false)
  })

  it('returns null for an empty non-explicit request (avoids popping up on every keystroke)', () => {
    expect(completeIdentifiers(contextAt(''), REGISTRY)).toBeNull()
  })

  it('suggests a namespace\'s own identifiers right after its dot, as variables when the namespace is signal/env', () => {
    const result = completeIdentifiers(contextAt('signal.'), REGISTRY)

    expect(result.options).toHaveLength(1)
    const [option] = result.options
    expect(option.label).toBe('mood')
    expect(option.type).toBe('variable')
    expect(option.apply).toBe('mood')
    // The full description lives in `info` (see completionInfo's own
    // tests below), never in `detail` — a completion's own `detail` is
    // rendered inline in the list and gets clipped with an ellipsis past
    // the list's own width, which is exactly what was cutting a longer
    // ui-description off.
    expect(option.detail).toBeUndefined()
    expect(typeof option.info).toBe('function')
  })

  it('suggests a proxy namespace\'s own identifiers as functions, appending ()', () => {
    const result = completeIdentifiers(contextAt('system.'), REGISTRY)

    expect(result.options.map((o) => ({ label: o.label, type: o.type, apply: o.apply }))).toEqual([
      { label: 'today', type: 'function', apply: 'today()' },
      { label: 'time', type: 'function', apply: 'time()' }
    ])
    expect(result.options.every((o) => typeof o.info === 'function')).toBe(true)
  })

  it('returns every identifier of the resolved namespace unfiltered, regardless of the partial word already typed after the dot', () => {
    const result = completeIdentifiers(contextAt('metric.ret'), REGISTRY)

    expect(new Set(result.options.map((o) => o.label))).toEqual(new Set(['retention', 'activity_consistency']))
  })

  it('resolves the nested session.metric namespace from a three-segment dotted path', () => {
    const result = completeIdentifiers(contextAt('session.metric.eng'), REGISTRY)

    expect(new Set(result.options.map((o) => o.label))).toEqual(new Set(['engagement', 'state_stability']))
    const engagementOption = result.options.find((o) => o.label === 'engagement')
    expect(engagementOption.apply).toBe('engagement()')
  })

  it('always offers "metric" itself right after session\'s own dot, alongside its real identifiers', () => {
    const result = completeIdentifiers(contextAt('session.'), REGISTRY)

    const labels = result.options.map((o) => o.label)
    expect(labels).toContain('number_of_user_sessions')
    expect(labels).toContain('metric')
    const metricOption = result.options.find((o) => o.label === 'metric')
    expect(metricOption.type).toBe('namespace')
    expect(metricOption.apply).toBe('metric')
  })

  it('returns null when the dotted namespace is not a real one in the registry', () => {
    expect(completeIdentifiers(contextAt('bogus.'), REGISTRY)).toBeNull()
  })

  it('positions the completion range right after the dot, not the whole dotted expression', () => {
    const result = completeIdentifiers(contextAt('signal.mo'), REGISTRY)
    // "signal.mo" is 9 characters; the replaceable range should start
    // right after the dot (index 7), not at the very start of "signal".
    expect(result.from).toBe(7)
  })

  it('works in the middle of a larger expression, not just at the very start', () => {
    const text = 'signal.mood >= 40 and sess'
    const result = completeIdentifiers(contextAt(text), REGISTRY)

    expect(result.options.map((o) => o.label)).toContain('session')
    // The replaceable range is just "sess" (the word actually being
    // typed), not the whole preceding expression.
    expect(result.from).toBe(text.length - 'sess'.length)
  })
})

describe('completionInfo', () => {
  it('renders a symbol tag, then the identifier bolded, then its full description', () => {
    const node = completionInfo('mood', 'How positive the user sounds.', 'variable')

    const symbol = node.querySelector('.cm-trigger-completion-info-symbol')
    const label = node.querySelector('strong')
    const description = node.querySelector('.cm-trigger-completion-info-description')
    expect(symbol.textContent).toBe('[var]')
    expect(label.textContent).toBe('mood')
    expect(description.textContent).toBe('How positive the user sounds.')
  })

  it('uses a distinct symbol for a proxy (function-typed) identifier', () => {
    const node = completionInfo('today', "Today's date.", 'function')
    expect(node.querySelector('.cm-trigger-completion-info-symbol').textContent).toBe('[fn]')
  })

  it('never truncates a long, multi-line description', () => {
    const long = 'Line one of the description.\nLine two, still fully present.\n' + 'x'.repeat(500)
    const node = completionInfo('engagement', long, 'function')

    expect(node.querySelector('.cm-trigger-completion-info-description').textContent).toBe(long)
  })

  it('omits the description block entirely when there is none', () => {
    const node = completionInfo('visits', '', 'variable')
    expect(node.querySelector('.cm-trigger-completion-info-description')).toBeNull()
  })
})

describe('isProxyNamespace', () => {
  it('is false for the two variable namespaces', () => {
    expect(isProxyNamespace('signal')).toBe(false)
    expect(isProxyNamespace('env')).toBe(false)
  })

  it('is true for every proxy namespace', () => {
    expect(isProxyNamespace('system')).toBe(true)
    expect(isProxyNamespace('session')).toBe(true)
    expect(isProxyNamespace('session.metric')).toBe(true)
    expect(isProxyNamespace('metric')).toBe(true)
  })
})

describe('namespaceOf (the coloring regex\'s own namespace extraction)', () => {
  it('extracts a plain single-segment namespace', () => {
    expect(namespaceOf('signal.mood')).toBe('signal')
    expect(namespaceOf('metric.retention')).toBe('metric')
  })

  it('extracts the nested session.metric namespace, not just session', () => {
    expect(namespaceOf('session.metric.engagement')).toBe('session.metric')
  })

  it('extracts plain session when not followed by .metric', () => {
    expect(namespaceOf('session.number_of_user_sessions')).toBe('session')
  })

  it('every namespace it can extract has a fixed color', () => {
    for (const namespace of ['signal', 'env', 'system', 'session', 'session.metric', 'metric']) {
      expect(NAMESPACE_COLORS[namespace]).toMatch(/^#[0-9a-f]{6}$/)
    }
  })
})
