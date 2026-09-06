import { describe, expect, it } from 'vitest'
import { EditorState } from '@codemirror/state'
import { CompletionContext } from '@codemirror/autocomplete'
import {
  completeIdentifiers, completionInfo, excludingNamespaces, isProxyNamespace, namespaceOf, NAMESPACE_COLORS
} from '../src/triggerEditorSupport.js'

const REGISTRY = {
  signal: { mood: 'How positive the user sounds.' },
  env: { visits: '' },
  system: { today: "Today's date.", time: 'The current time.' },
  session: { number_of_user_sessions: 'How many sessions.' },
  'session.metric': { engagement: 'Engagement score.', state_stability: 'State stability score.' },
  metric: { retention: 'Retention score.', activity_consistency: 'Activity consistency score.' }
}

// Prompt 6's cross-project namespace (see ProjectService.get_active_
// identifier_registry) — a real registry never carries "automaton"
// unless there's at least one other project, so this is its own fixture
// rather than folded into REGISTRY above (every test that doesn't care
// about it keeps seeing the exact same suggestions it always did).
const REGISTRY_WITH_AUTOMATON = {
  ...REGISTRY,
  automaton: {},
  'automaton.other_project': { state: "The 'other_project' project's own current state." },
  'automaton.other_project.env': { budget: 'Remaining budget, shared cross-project.' }
}

// A declared source's own dynamic namespace (see backend
// ProjectInspector.get_identifier_registry) — same "source" (empty) +
// "source.<name>" (that source's own methods) shape as automaton above.
const REGISTRY_WITH_SOURCE = {
  ...REGISTRY,
  source: {},
  'source.pino': {
    read: "This source's own archive file, read as plain text.",
    select: "Grep over this source's own archive file."
  }
}

const TOP_LEVEL = new Set(['signal', 'env', 'system', 'session', 'metric'])

function contextAt(text, explicit = false) {
  const state = EditorState.create({ doc: text })
  return new CompletionContext(state, text.length, explicit)
}

function optionsAt(text, registry = REGISTRY, explicit = false) {
  return completeIdentifiers(contextAt(text, explicit), registry).options
}

function labelsAt(text, registry = REGISTRY, explicit = false) {
  return new Set(optionsAt(text, registry, explicit).map((o) => o.label))
}

function optionAt(text, label, registry = REGISTRY) {
  return optionsAt(text, registry).find((o) => o.label === label)
}

describe('completeIdentifiers', () => {
  it('suggests every top-level namespace unfiltered for a bare word or an explicit empty request, and nothing at all on an implicit empty one', () => {
    // CodeMirror's own autocomplete does the actual text matching
    // downstream (see @codemirror/autocomplete's CompletionResult.options).
    expect(labelsAt('sig')).toEqual(TOP_LEVEL)
    expect(labelsAt('', REGISTRY, true)).toEqual(TOP_LEVEL)
    // "session.metric" is a dotted registry key, never itself a bare
    // namespace word a user would type directly.
    expect(labelsAt('', REGISTRY, true).has('session.metric')).toBe(false)
    expect(completeIdentifiers(contextAt(''), REGISTRY)).toBeNull()

    const signalOption = optionAt('sig', 'signal')
    expect(signalOption.type).toBe('namespace')
    // No trailing "." — completing a namespace name shouldn't force the
    // user into typing on that field before they can look elsewhere.
    expect(signalOption.apply).toBe('signal')
  })

  it('suggests a namespace\'s own identifiers after its dot — plain for signal/env, call-style for a proxy — with the description in info, never detail', () => {
    // A completion's own `detail` is rendered inline in the list and gets
    // clipped past the list's width, which is what cut a longer
    // ui-description off; the full text lives in `info` instead.
    const [mood] = optionsAt('signal.')
    expect(mood.label).toBe('mood')
    expect(mood.type).toBe('variable')
    expect(mood.apply).toBe('mood')
    expect(mood.detail).toBeUndefined()
    expect(typeof mood.info).toBe('function')

    const system = optionsAt('system.')
    expect(system.map((o) => ({ label: o.label, type: o.type, apply: o.apply }))).toEqual([
      { label: 'today', type: 'function', apply: 'today()' },
      { label: 'time', type: 'function', apply: 'time()' }
    ])
    expect(system.every((o) => typeof o.info === 'function')).toBe(true)
  })

  it('returns every identifier of the resolved namespace unfiltered, resolving a nested one from its full dotted path', () => {
    expect(labelsAt('metric.ret')).toEqual(new Set(['retention', 'activity_consistency']))
    expect(labelsAt('session.metric.eng')).toEqual(new Set(['engagement', 'state_stability']))
    expect(optionAt('session.metric.eng', 'engagement').apply).toBe('engagement()')
  })

  it('always offers "metric" itself right after session\'s own dot, alongside its real identifiers', () => {
    expect(labelsAt('session.')).toContain('number_of_user_sessions')
    const metricOption = optionAt('session.', 'metric')
    expect(metricOption.type).toBe('namespace')
    expect(metricOption.apply).toBe('metric')
  })

  it('returns null for an unknown dotted namespace and otherwise replaces only the word being typed', () => {
    expect(completeIdentifiers(contextAt('bogus.'), REGISTRY)).toBeNull()

    // "signal.mo" is 9 characters; the replaceable range starts right
    // after the dot (index 7), not at the very start of "signal".
    expect(completeIdentifiers(contextAt('signal.mo'), REGISTRY).from).toBe(7)

    const text = 'signal.mood >= 40 and sess'
    const result = completeIdentifiers(contextAt(text), REGISTRY)
    expect(result.options.map((o) => o.label)).toContain('session')
    expect(result.from).toBe(text.length - 'sess'.length)
  })

  it('descends automaton.<project>.env one namespace at a time, offering state and every declared env key as plain identifiers', () => {
    expect(labelsAt('auto', REGISTRY_WITH_AUTOMATON)).toContain('automaton')

    const [project] = optionsAt('automaton.', REGISTRY_WITH_AUTOMATON)
    expect(project.label).toBe('other_project')
    expect(project.type).toBe('namespace')
    // No trailing "()" — automaton.<project> is a namespace to descend
    // into, never itself called.
    expect(project.apply).toBe('other_project')

    const stateOption = optionAt('automaton.other_project.', 'state', REGISTRY_WITH_AUTOMATON)
    expect(stateOption.type).toBe('variable')
    expect(stateOption.apply).toBe('state')
    const envOption = optionAt('automaton.other_project.', 'env', REGISTRY_WITH_AUTOMATON)
    expect(envOption.type).toBe('namespace')
    expect(envOption.apply).toBe('env')

    const [budget] = optionsAt('automaton.other_project.env.', REGISTRY_WITH_AUTOMATON)
    expect(budget.label).toBe('budget')
    expect(budget.type).toBe('variable')
    expect(budget.apply).toBe('budget')
  })

  it('offers every declared source as a child namespace, then that source\'s own methods call-style', () => {
    const [pino] = optionsAt('source.', REGISTRY_WITH_SOURCE)
    expect(pino.label).toBe('pino')
    expect(pino.type).toBe('namespace')
    expect(pino.apply).toBe('pino')

    const methods = optionsAt('source.pino.', REGISTRY_WITH_SOURCE)
    expect(methods.map((o) => o.label).sort()).toEqual(['read', 'select'])
    const readOption = methods.find((o) => o.label === 'read')
    expect(readOption.type).toBe('function')
    expect(readOption.apply).toBe('read()')
  })
})

describe('completionInfo', () => {
  it('renders a type symbol, the bolded identifier and its full untruncated description, omitting the block when empty', () => {
    const node = completionInfo('mood', 'How positive the user sounds.', 'variable')
    expect(node.querySelector('.cm-trigger-completion-info-symbol').textContent).toBe('[var]')
    expect(node.querySelector('strong').textContent).toBe('mood')
    expect(node.querySelector('.cm-trigger-completion-info-description').textContent).toBe('How positive the user sounds.')

    expect(completionInfo('today', "Today's date.", 'function')
      .querySelector('.cm-trigger-completion-info-symbol').textContent).toBe('[fn]')

    const long = 'Line one of the description.\nLine two, still fully present.\n' + 'x'.repeat(500)
    expect(completionInfo('engagement', long, 'function')
      .querySelector('.cm-trigger-completion-info-description').textContent).toBe(long)

    expect(completionInfo('visits', '', 'variable').querySelector('.cm-trigger-completion-info-description')).toBeNull()
  })
})

describe('isProxyNamespace', () => {
  it('is true only for the namespaces whose members are actually called', () => {
    // signal/env/user resolve straight off an already-fetched dict;
    // automaton.* is real attribute access; datetime.timezone's only
    // member (utc) is a plain attribute.
    for (const namespace of ['session', 'session.metric', 'source', 'metric', 'datetime']) {
      expect(isProxyNamespace(namespace)).toBe(true)
    }
    for (const namespace of [
      'signal', 'env', 'user', 'automaton', 'automaton.other_project', 'automaton.other_project.env', 'datetime.timezone'
    ]) {
      expect(isProxyNamespace(namespace)).toBe(false)
    }
  })
})

describe('namespaceOf (the coloring regex\'s own namespace extraction)', () => {
  it('extracts the longest namespace it recognizes, nested ones included, and every one it can extract has a fixed color', () => {
    expect(namespaceOf('signal.mood')).toBe('signal')
    expect(namespaceOf('metric.retention')).toBe('metric')
    expect(namespaceOf('user.email')).toBe('user')
    expect(namespaceOf("source.attachment('notes.txt')")).toBe('source')
    expect(namespaceOf('session.metric.engagement')).toBe('session.metric')
    expect(namespaceOf('session.number_of_user_sessions')).toBe('session')
    expect(namespaceOf('datetime.timezone.utc')).toBe('datetime.timezone')
    expect(namespaceOf('datetime.datetime')).toBe('datetime')
    expect(namespaceOf('datetime.timedelta')).toBe('datetime')
    // .state/.env.<key> stay uncolored past automaton itself.
    expect(namespaceOf('automaton.other_project.state')).toBe('automaton')
    expect(namespaceOf('automaton.other_project.env.budget')).toBe('automaton')

    for (const namespace of ['signal', 'env', 'session', 'session.metric', 'user', 'source', 'metric', 'automaton', 'datetime', 'datetime.timezone']) {
      expect(NAMESPACE_COLORS[namespace]).toMatch(/^#[0-9a-f]{6}$/)
    }
  })
})

describe('excludingNamespaces', () => {
  it('drops a namespace and everything nested under it by dotted prefix, returning the registry untouched with nothing to exclude', () => {
    expect(Object.keys(excludingNamespaces(REGISTRY, ['session']))).toEqual(['signal', 'env', 'system', 'metric'])
    expect(excludingNamespaces(REGISTRY, [])).toBe(REGISTRY)
    expect(excludingNamespaces(REGISTRY, undefined)).toBe(REGISTRY)

    const registry = { ...REGISTRY, sessionish: { x: '' }, actuator: { notify: '' } }
    const filtered = excludingNamespaces(registry, ['session'])
    // What the on-enter editor sees: session gone, session.metric gone
    // with it, actuator kept — and never a mere string-prefix match.
    expect(Object.keys(filtered)).toContain('sessionish')
    expect(filtered.actuator).toBeDefined()
    expect(filtered.session).toBeUndefined()
    expect(filtered['session.metric']).toBeUndefined()
  })
})
