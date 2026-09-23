import { beforeEach, describe, expect, it } from 'vitest'
import { EditorState } from '@codemirror/state'
import { CompletionContext } from '@codemirror/autocomplete'
import { installTriggerNamespaces } from '../src/triggerNamespaces.js'
import {
  completeIdentifiers, completionInfo, excludingNamespaces, excludingIdentifiers, isProxyNamespace, namespaceOf, namespaceColor
} from '../src/triggerEditorSupport.js'

const contributed = {
  name: 'partner',
  color: '#123456',
  proxy: false,
  emptyLabel: '(nothing to reference yet)',
  emptyHint: 'Declare a partner first.'
}

beforeEach(() => installTriggerNamespaces([contributed]))

const REGISTRY = {
  signal: { mood: 'How positive the user sounds.' },
  env: { visits: '' },
  system: { today: "Today's date.", time: 'The current time.' },
  session: { number_of_user_sessions: 'How many sessions.' },
  'session.metric': { engagement: 'Engagement score.', state_stability: 'State stability score.' },
  metric: { retention: 'Retention score.', activity_consistency: 'Activity consistency score.' }
}

const REGISTRY_WITH_CONTRIBUTED = {
  ...REGISTRY,
  partner: {},
  'partner.other_project': { state: "The 'other_project' project's own current state." },
  'partner.other_project.env': { budget: 'Remaining budget, shared cross-project.' }
}

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
    expect(labelsAt('sig')).toEqual(TOP_LEVEL)
    expect(labelsAt('', REGISTRY, true)).toEqual(TOP_LEVEL)
    expect(labelsAt('', REGISTRY, true).has('session.metric')).toBe(false)
    expect(completeIdentifiers(contextAt(''), REGISTRY)).toBeNull()

    const signalOption = optionAt('sig', 'signal')
    expect(signalOption.type).toBe('namespace')
    expect(signalOption.apply).toBe('signal')
  })

  it('suggests a namespace\'s own identifiers after its dot — plain for signal/env, call-style for a proxy — with the description in info, never detail', () => {
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

    expect(completeIdentifiers(contextAt('signal.mo'), REGISTRY).from).toBe(7)

    const text = 'signal.mood >= 40 and sess'
    const result = completeIdentifiers(contextAt(text), REGISTRY)
    expect(result.options.map((o) => o.label)).toContain('session')
    expect(result.from).toBe(text.length - 'sess'.length)
  })

  it('descends a contributed namespace one level at a time, offering its members as plain identifiers', () => {
    expect(labelsAt('par', REGISTRY_WITH_CONTRIBUTED)).toContain('partner')

    const [project] = optionsAt('partner.', REGISTRY_WITH_CONTRIBUTED)
    expect(project.label).toBe('other_project')
    expect(project.type).toBe('namespace')
    expect(project.apply).toBe('other_project')

    const stateOption = optionAt('partner.other_project.', 'state', REGISTRY_WITH_CONTRIBUTED)
    expect(stateOption.type).toBe('variable')
    expect(stateOption.apply).toBe('state')
    const envOption = optionAt('partner.other_project.', 'env', REGISTRY_WITH_CONTRIBUTED)
    expect(envOption.type).toBe('namespace')
    expect(envOption.apply).toBe('env')

    const [budget] = optionsAt('partner.other_project.env.', REGISTRY_WITH_CONTRIBUTED)
    expect(budget.label).toBe('budget')
    expect(budget.type).toBe('variable')
    expect(budget.apply).toBe('budget')
  })

  it('offers a contributed namespace its own empty hint when it has nothing to declare', () => {
    const empty = { ...REGISTRY, partner: {} }
    const [only] = optionsAt('partner.', empty)
    expect(only.label).toBe('(nothing to reference yet)')
    expect(only.info().querySelector('.cm-trigger-completion-info-description').textContent)
      .toBe('Declare a partner first.')

    expect(completeIdentifiers(contextAt('signal.unknown.'), empty)).toBeNull()
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
    for (const namespace of ['session', 'session.metric', 'source', 'metric', 'datetime']) {
      expect(isProxyNamespace(namespace)).toBe(true)
    }
    for (const namespace of [
      'signal', 'env', 'user', 'partner', 'partner.other_project', 'partner.other_project.env', 'datetime.timezone'
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
    expect(namespaceOf('partner.other_project.state')).toBe('partner')
    expect(namespaceOf('partner.other_project.env.budget')).toBe('partner')

    for (const namespace of ['signal', 'env', 'session', 'session.metric', 'user', 'source', 'task', 'chat', 'metric', 'partner', 'datetime', 'datetime.timezone']) {
      expect(namespaceColor(namespace)).toMatch(/^#[0-9a-f]{6}$/)
    }
  })
})

describe('excludingNamespaces', () => {
  it('drops a namespace and everything nested under it by dotted prefix, returning the registry untouched with nothing to exclude', () => {
    expect(Object.keys(excludingNamespaces(REGISTRY, ['session']))).toEqual(['signal', 'env', 'system', 'metric'])
    expect(excludingNamespaces(REGISTRY, [])).toBe(REGISTRY)
    expect(excludingNamespaces(REGISTRY, undefined)).toBe(REGISTRY)

    const registry = { ...REGISTRY, sessionish: { x: '' }, task: { send_mail: '' } }
    const filtered = excludingNamespaces(registry, ['session'])
    expect(Object.keys(filtered)).toContain('sessionish')
    expect(filtered.task).toBeDefined()
    expect(filtered.session).toBeUndefined()
    expect(filtered['session.metric']).toBeUndefined()
  })
})

describe('a no-argument chat call', () => {
  it('completes as the finished call, parentheses and all, so chat.clear() is typed by picking it', () => {
    const registry = { ...REGISTRY, chat: { clear: 'Blanks the chat window.', notify: 'Shows a toast.' } }
    const option = optionAt('chat.cl', 'clear', registry)

    expect(option.type).toBe('function')
    expect(option.apply).toBe('clear()')
  })
})

describe('excludingIdentifiers', () => {
  it('drops one identifier from its own namespace, leaving the rest and every other namespace untouched', () => {
    const registry = { ...REGISTRY, chat: { write: 'Writes the reply.', notify: 'Shows a toast.' } }
    const filtered = excludingIdentifiers(registry, ['chat.write'])
    expect(Object.keys(filtered.chat)).toEqual(['notify'])
    expect(filtered.signal).toBe(registry.signal)

    expect(excludingIdentifiers(registry, [])).toBe(registry)
    expect(excludingIdentifiers(registry, undefined)).toBe(registry)
    expect(excludingIdentifiers(registry, ['unknown.thing'])).toEqual(registry)
  })
})
