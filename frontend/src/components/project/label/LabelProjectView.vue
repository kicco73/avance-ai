<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChatTimeline from '../../chat/ChatTimeline.vue'
import SessionsPanel from '../../chat/SessionsPanel.vue'
import MessageCommentButton from '../../chat/MessageCommentButton.vue'
import Inspector from '../../inspector/Inspector.vue'
import InspectorGraphTab from '../../inspector/InspectorGraphTab.vue'
import InspectorSignalsTab from '../../inspector/InspectorSignalsTab.vue'
import InspectorMetricsTab from '../../inspector/InspectorMetricsTab.vue'
import InspectorDetailCard from '../../inspector/InspectorDetailCard.vue'
import CardMenu from '../../inspector/CardMenu.vue'
import { vAutosize } from '../../inspector/textareaAutosize.js'
import { handleEnterNext } from '../../inspector/enterToNextField.js'
import ErrorBanner from '../../ErrorBanner.vue'
import {
  getMessages, getSessionSignals, getSessions, getProjectGraph, postImportSession, postImportSessionJson,
  getExportSessions, putMessageExpectedState, putMessageExpectedSignals, putMessageComment, putSessionLabeled,
  putSessionTitle, putSessionComment, deleteSessionAnnotations, deleteSession
} from '../../../api.js'
import { currentSessionId, sessions, sessionsLoading, loadSessions, refreshSessionsQuietly, selectSession } from '../../../chatStore.js'
import {
  buildTimeline, commentForMessage, highlightedStateKeyFor, nearestMessageIdAtOrBefore, signalValuesFor
} from '../../../benchmarkTimeline.js'
import { summarizeImportFailures } from '../../../sessionImport.js'
import { setApiError, clearApiError } from '../../../errorStore.js'

const props = defineProps({
  projectName: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['close'])

const loading = ref(true)
// Raw backend rows (id, role, content, audio_text, timestamp,
// session_id) — see db.get_messages. Kept as-is (not chatStore.js's live
// `messages` shape) since this view reviews a fixed past session, never
// the live conversation.
const rawMessages = ref([])
// The session's full Signals event log (id, timestamp, values,
// expected_values, expected_state, old_state, action, new_state,
// message_id) — see db.get_signals — from which the timeline's
// transition rows, every point-in-time signal-values reconstruction, and
// every annotation (see annotatableSignalsRow) are derived, with no
// further backend round trips.
const signalsLog = ref([])
const sessionStartState = ref(null)
// Project-wide, fetched once (see onMounted below) — whether a live turn
// evaluates on the assistant's own reply (true) or the user's own
// message (false). An imported session (see ChatSession.source) has no
// real Tracking rows to consult at all, so annotatableSignalsRow below
// falls back to this same convention instead, to decide which side of
// an imported session's own messages is a legitimate mark point.
const autotrackingOnAiMessage = ref(false)

const inspectorRef = ref(null)
const inspectorWidth = ref(360)
const inspectorCollapsed = ref(false)
// This view's own tab set — Metrics instead of Env (see Inspector.vue's
// own slot-based contract; EditProjectView.vue passes a different set for
// its own live chat). An imported session (see currentSessionIsImported
// below) has no real avance-computed metrics history of its own to show
// at all — Metrics reads off live Tracking rows an import never produces
// (see MetricService) — so only States/Signals (annotation surfaces)
// make sense there. Inspector.vue's own tabs watcher already falls back
// to the first tab whenever the active one stops being valid, so
// switching sessions never needs to reset inspectorActiveTab by hand.
const inspectorTabs = computed(() => {
  const base = [
    { id: 'states', label: 'States' },
    { id: 'signals', label: 'Signals' }
  ]
  const withMetrics = currentSessionIsImported.value ? base : [...base, { id: 'metrics', label: 'Metrics' }]
  return [...withMetrics, { id: 'info', label: 'Info' }]
})
const inspectorActiveTab = ref('states')
// The Sessions panel starts open — reviewing a specific session is the
// point of this view, so the picker should always be immediately visible
// rather than tucked behind a toggle.
const benchmarkSessionsPanelOpen = ref(true)
const sessionsPanelWidth = ref(240)
let dragTarget = null

function startInspectorDrag(event) {
  dragTarget = 'inspector'
  event.preventDefault()
}

function startSessionsDrag(event) {
  dragTarget = 'sessions'
  event.preventDefault()
}

function onDrag(event) {
  if (dragTarget === 'inspector') {
    inspectorWidth.value = Math.min(560, Math.max(240, inspectorWidth.value - event.movementX))
    inspectorRef.value?.resize()
  } else if (dragTarget === 'sessions') {
    sessionsPanelWidth.value = Math.min(420, Math.max(160, sessionsPanelWidth.value + event.movementX))
  }
}

function stopDrag() {
  dragTarget = null
}

// Toggled by SessionsPanel.vue's own collapse button, same as
// ChatWindow.vue's own panel — but a local, independent open/closed flag:
// the main page's own Sessions panel (see chatStore.js's
// sessionsPanelOpen) is a separate piece of UI, hidden behind this
// full-screen overlay while it's open, and shouldn't change just because
// this view's own panel did.
function toggleBenchmarkSessionsPanel() {
  benchmarkSessionsPanelOpen.value = !benchmarkSessionsPanelOpen.value
  if (benchmarkSessionsPanelOpen.value) loadSessions(true)
}

// This view's own Sessions panel is the one place that reviews imported
// transcripts (see ChatSession.source) alongside live ones (see
// SessionsPanel.vue's own allowImport) — every load/refresh below passes
// includeImported so an imported session doesn't disappear from the list
// again after the very next reload.
//
// One .txt transcript, exactly one session — pushes its own {file, ok,
// error?} onto the shared `results` accumulator (see handleImportSession
// below) and returns the new session_id, or null on failure.
async function importTranscriptFile(file, results) {
  try {
    const { session_id } = await postImportSession(file)
    results.push({ file, ok: true })
    return session_id
  } catch (err) {
    // apiFetch already wrote this file's own failure to the shared error
    // banner (see api.js) — summarizeImportFailures below builds the
    // batch's own summary from `results` once every file is done, which
    // is what should actually stay on screen when the batch has more
    // than one item.
    results.push({ file, ok: false, error: err.message })
    return null
  }
}

// A "Download all" .json export is a whole *array* of sessions in one
// file (see backend tracking/session_export.py), unlike a .txt transcript
// (always exactly one) — so this pushes one result per session found
// inside it, each under its own pseudo-file label (`{name}`, mirroring
// what summarizeImportFailures already reads off a real File) since no
// real per-session File object exists to blame a failure on otherwise.
// Same "one bad item never aborts the rest" contract as the .txt path,
// just one level deeper.
async function importJsonFile(file, results) {
  let sessionsData
  try {
    const parsed = JSON.parse(await file.text())
    if (!Array.isArray(parsed)) throw new Error('Expected a JSON array of sessions.')
    sessionsData = parsed
  } catch (err) {
    results.push({ file, ok: false, error: err.message })
    return null
  }
  let lastId = null
  for (const [index, sessionData] of sessionsData.entries()) {
    const label = { name: sessionData?.name || `${file.name} #${index + 1}` }
    try {
      const { session_id } = await postImportSessionJson(sessionData)
      results.push({ file: label, ok: true })
      lastId = session_id
    } catch (err) {
      results.push({ file: label, ok: false, error: err.message })
    }
  }
  return lastId
}

// `files` is a whole batch at once (see SessionsPanel.vue's own `multiple`
// file input, now accepting .json alongside .txt) — one bad item must
// never abort the rest (see importTranscriptFile/importJsonFile above).
// The session list is only refreshed once, after every file has settled:
// N sequential refreshes would just be N-1 wasted round trips for a list
// that only needs to be right at the end.
async function handleImportSession(files) {
  const results = []
  let lastImportedId = null
  for (const file of files) {
    const sessionId = file.name.toLowerCase().endsWith('.json')
      ? await importJsonFile(file, results)
      : await importTranscriptFile(file, results)
    if (sessionId != null) lastImportedId = sessionId
  }

  if (lastImportedId != null) {
    // The list must actually contain the new session before it can be
    // looked up in it — refresh first, select second, not the other way
    // around. Only the most recently imported session is selected, same
    // as the single-file flow this replaces.
    await refreshSessionsQuietly(true)
    const imported = sessions.value.find((s) => s.id === lastImportedId)
    if (imported) selectSession(imported)
  }

  const failureSummary = summarizeImportFailures(results)
  if (failureSummary) setApiError(failureSummary.message, failureSummary.detail)
  else clearApiError()
}

// Picking a session here uses the exact same shared mechanism as every
// other session picker in the app (see chatStore.js's own selectSession,
// used by SessionsPanel.vue's other callers) — currentSessionId is the
// one source of truth, and the watcher below reacts to it changing.
function onSelectSession(session) {
  selectSession(session)
}

// Only an imported session is ever deletable here (see SessionsPanel.
// vue's own deleteImportedOnly) — a live/native one is the record of a
// real conversation, not this view's own to discard. Mirrors chatStore.
// js's own handleDeleteSession, just against this view's own session
// list (refreshSessionsQuietly(true) — see handleImportSession's own
// docstring on why includeImported matters here) rather than the main
// chat's.
const deletingSessionId = ref(null)
async function handleDeleteSession(session) {
  if (!window.confirm(`Delete this imported session (${session.title || session.end_state})? This cannot be undone.`)) return
  deletingSessionId.value = session.id
  try {
    await deleteSession(session.id)
    if (session.id === currentSessionId.value) currentSessionId.value = null
    await refreshSessionsQuietly(true)
  } catch {
    // already surfaced via apiFetch
  } finally {
    deletingSessionId.value = null
  }
}

function handleWindowResize() {
  inspectorRef.value?.resize()
}

async function loadTimeline() {
  const sessionId = currentSessionId.value
  if (sessionId == null) {
    rawMessages.value = []
    signalsLog.value = []
    sessionStartState.value = null
    loading.value = false
    return
  }
  loading.value = true
  selected.value = null
  try {
    const [messageRows, signalRows, allSessions] = await Promise.all([
      getMessages(sessionId),
      getSessionSignals(sessionId),
      getSessions(true)
    ])
    rawMessages.value = messageRows
    signalsLog.value = signalRows
    sessionStartState.value = allSessions.find((s) => s.id === sessionId)?.start_state ?? null
    // The core Metrics tab (project-wide, but "live" means "as of now" —
    // a stale point-in-time cutoff from the previous session's own
    // selection would otherwise linger) needs a fresh fetch for *this*
    // session — it doesn't reactively recompute on its own (see
    // InspectorMetricsTab.vue's own refresh(active), a no-op unless its
    // own tab is the one currently showing). Relying on the `selected`
    // reset above alone isn't enough: switching sessions while nothing
    // was ever selected leaves `selected` at null both before and after,
    // so that watcher never fires at all.
    await nextTick()
    inspectorRef.value?.refresh()
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}

// A session switch (from this view's own Sessions panel, or the main
// page's — currentSessionId is shared, see onSelectSession) always shows
// *that* session's own timeline from scratch — whatever was selected
// before belonged to a different session's history.
watch(currentSessionId, loadTimeline)

// Chronological, merged view of the session's messages and its state
// transitions — real ones, plus any evaluation point an expert annotated
// even though nothing actually changed there. See benchmarkTimeline.js
// for the actual logic (and its own regression tests) — every function
// there is pure, taking rawMessages/signalsLog/sessionStartState
// explicitly instead of closing over these refs.
const timeline = computed(() =>
  buildTimeline(rawMessages.value, signalsLog.value, sessionStartState.value, { imported: currentSessionIsImported.value })
)

// The point in time currently reflected by the Inspector — a message or a
// transition clicked in the timeline (see selectMessage/selectTransition).
// null until the first click, showing just the project's own definitions
// with nothing highlighted.
const selected = ref(null)

function selectMessage(message) {
  selected.value = { kind: 'message', message }
}

function selectTransition(transition) {
  selected.value = { kind: 'transition', transition }
}

// See benchmarkTimeline.js for the actual logic (and its own regression
// tests) behind highlightedStateKey/signalValues below — both exist
// specifically to avoid landing one point behind the current selection's
// own evaluation (see highlightedStateKeyFor/signalValuesFor's own
// docstrings).
const highlightedStateKey = computed(() =>
  highlightedStateKeyFor(selected.value, timeline.value, sessionStartState.value)
)

// Only a transition has "the action that produced it" to highlight.
// old_state === '' (the automaton's own init transition) is a real,
// clickable edge in the graph too now — a transparent pseudo-node's own
// outgoing edge (see InspectorGraphTab.vue's isInitEdge) — so this no
// longer excludes it: every transition selection highlights *some* edge.
const firedActionEdge = computed(() => {
  if (selected.value?.kind !== 'transition') return null
  const t = selected.value.transition
  return { stateKey: t.old_state, actionName: t.action }
})

const untilMessageId = computed(() => {
  if (!selected.value) return null
  if (selected.value.kind === 'message') return selected.value.message.id
  // A transition auto-tracking produced is linked straight back to the
  // message whose evaluation caused it (see db.py's Signals.message) — an
  // exact lookup, only falling back to the nearest-before heuristic for a
  // manual action's transition, which was never evaluated from any
  // message at all.
  return selected.value.transition.message_id ?? nearestMessageIdAtOrBefore(rawMessages.value, selected.value.transition.timestamp)
})

const signalValues = computed(() => signalValuesFor(selected.value, signalsLog.value, rawMessages.value))

// The currently-selected session's own row out of the shared sessions
// list (chatStore.js) — id/source/title/datetime_start/datetime_end/
// start_state/end_state/labeled, see chat_service.py's own
// _session_payload. Null before the list has loaded, or if the id it's
// pinned to has since been deleted out from under it.
const currentSession = computed(() => sessions.value.find((s) => s.id === currentSessionId.value) ?? null)

// Whether the session currently being reviewed was imported (see
// ChatSession.source) rather than played live — the one case with no
// real Tracking rows at all to consult for annotatableSignalsRow below
// (see tracking.session_import's own module docstring).
const currentSessionIsImported = computed(() => currentSession.value?.source === 'imported')

// The Info tab's own read-only start/end state cards (see the #tab-info
// template below) — resolved through the "States" tab's own already-
// loaded graph (statesTabRef, see the template's registerTab wiring)
// rather than a second getProjectGraph fetch of their own.
const statesTabRef = ref(null)
// A plain script-scope setter, not `statesTabRef.value = el` written
// directly in the template below — Vite's dev-mode (non-inlined)
// <script setup> compile target resolves a template's own bare
// identifiers through a ref-auto-unwrapping $setup proxy, so an
// explicit `.value` write *inside a template expression* assigns onto
// the already-unwrapped value (null, straight off ref(null)) instead of
// the ref itself, throwing "Cannot set properties of null" the moment
// this view ever mounts. A real function body, called from the
// template rather than inlined into it, is genuine script scope in
// every compile mode — see InspectorSignalsTab.vue's own setBlockRef/
// setLabelInputRef for the same convention.
function setStatesTabRef(el) {
  statesTabRef.value = el
}

// An imported session's own ChatSession.start_state/end_state are always
// null (see tracking.session_import — it never ran against the automaton
// at all), so there's nothing real to show there; a domain expert's own
// expected_state annotations (see signalsLog, chronological already) are
// the only "start"/"end" a session like that ever gets. A native
// session's own real start_state/end_state need no such substitute.
const importedAnnotatedStates = computed(() => signalsLog.value.map((row) => row.expected_state).filter(Boolean))
const sessionStartStateKey = computed(() =>
  currentSessionIsImported.value ? (importedAnnotatedStates.value[0] ?? null) : (currentSession.value?.start_state ?? null)
)
const sessionEndStateKey = computed(() =>
  currentSessionIsImported.value ? (importedAnnotatedStates.value.at(-1) ?? null) : (currentSession.value?.end_state ?? null)
)
const startStateElement = computed(() => statesTabRef.value?.stateElementFor(sessionStartStateKey.value) ?? null)
const endStateElement = computed(() => statesTabRef.value?.stateElementFor(sessionEndStateKey.value) ?? null)

// Same "best-effort local format, fall back to the raw ISO string on a
// bad/missing date" convention as SessionsPanel.vue's own
// formatSessionTimestamp — this file's own "Info" tab reuses the idea
// rather than that function itself (not exported there).
function formatSessionTimestamp(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

// An imported session has neither (see tracking.session_import) — no
// real conversation window to show at all, rather than a pair of em
// dashes implying one just wasn't recorded.
const currentSessionHasTimestamps = computed(() =>
  currentSession.value?.datetime_start != null || currentSession.value?.datetime_end != null
)

// A message is a legitimate mark point for an imported session (which
// has no real Tracking row to prove it) only on whichever side a live
// turn would actually have evaluated on — assistant if
// autotrackingOnAiMessage, user otherwise (see TrackingService.
// _materialize_imported_session_row, the backend's own mirror of this
// same rule).
function isImportedAnnotationPoint(message) {
  return message.role === (autotrackingOnAiMessage.value ? 'assistant' : 'user')
}

// The Signals row backing the current selection's own evaluation, if
// any — the row itself for a clicked transition auto-tracking produced
// (see its own message_id), or (found by message_id) the row a clicked
// message's own evaluation produced. null when there's no evaluation to
// annotate against at all — a manual action's transition (message_id
// null: see project_service.apply_manual_action), or a message
// auto-tracking never evaluated anything after (see Signals.message's own
// docstring) — the Inspector's annotation controls only ever show for a
// non-null value here. An imported session never has a real row for any
// message (see currentSessionIsImported) — a virtual one (no id yet,
// materialized backend-side the first time an annotation is actually
// written, see TrackingService._materialize_imported_session_row) steps
// in for whichever message is a legitimate mark point on its own session.
const annotatableSignalsRow = computed(() => {
  if (!selected.value) return null
  if (selected.value.kind === 'transition') {
    return selected.value.transition.message_id != null ? selected.value.transition : null
  }
  const message = selected.value.message
  const row = signalsLog.value.find((s) => s.message_id === message.id)
  if (row) return row
  if (currentSessionIsImported.value && isImportedAnnotationPoint(message)) {
    return { id: null, message_id: message.id, old_state: null, new_state: null, expected_state: null, expected_values: null, values: null }
  }
  return null
})

// The message id to PUT an annotation change against — the annotation
// API is message-centric (see api.js's putMessageExpectedState/
// putMessageExpectedSignals), so a transition selection still resolves
// back to whichever message its own row says caused it.
const annotatableMessageId = computed(() => {
  if (!annotatableSignalsRow.value) return null
  return selected.value.kind === 'message' ? selected.value.message.id : annotatableSignalsRow.value.message_id
})

const expectedState = computed(() => annotatableSignalsRow.value?.expected_state ?? null)
const expectedValues = computed(() => {
  const raw = annotatableSignalsRow.value?.expected_values
  return raw ? JSON.parse(raw) : {}
})

// The automaton's own starting point (old_state === "" — see
// resolveTransitionRow/syntheticSessionStartEntry, real or synthetic
// alike) has no real signal evaluation behind it at all — nothing was
// ever computed there to have an opinion about. An expert can still
// disagree about *where the automaton starts* (the expected-state
// control above), just never about signal values that don't exist.
const annotatableExpectedSignals = computed(() => {
  return annotatableSignalsRow.value != null && annotatableSignalsRow.value.old_state !== ''
})

// A full reload (rather than patching signalsLog in place) is needed
// because an annotation write can now change *which* Signals row exists
// for a message, not just its fields: annotating a session's own start
// point materializes a brand-new row (see backend ChatService.
// _materialize_session_start_row — the synthetic entry above has no real
// row/id yet), and clearing the last annotation on that same kind of row
// deletes it again (see _finalize_annotation_write). Re-selects the
// current transition by message_id (its own row id may have just changed
// underneath it) so the Inspector doesn't keep showing a stale snapshot.
async function reloadSignalsLog() {
  if (!currentSessionId.value) return
  signalsLog.value = await getSessionSignals(currentSessionId.value)
  if (selected.value?.kind === 'transition') {
    const messageId = selected.value.transition.message_id
    const match = timeline.value.find((e) => e.kind === 'transition' && e.transition.message_id === messageId)
    selected.value = match ? { kind: 'transition', transition: match.transition } : null
  }
  // Every caller of this is an annotation write (see onUpdateExpectedState/
  // onUpdateExpectedSignals/onUnlabelAll) — the Sessions panel's own
  // has_annotations tag for this exact session (see SessionsPanel.vue) can
  // only just have flipped either way, and won't otherwise refresh until
  // the panel is toggled closed and reopened. Quiet: a full loadSessions()
  // would flash the panel to "Loading…" for something the user never
  // asked to reload.
  await refreshSessionsQuietly(true)
}

async function onUpdateExpectedState(value) {
  const messageId = annotatableMessageId.value
  if (messageId == null) return
  try {
    await putMessageExpectedState(messageId, value)
    await reloadSignalsLog()
    inspectorRef.value?.refresh()
  } catch {
    // already surfaced via apiFetch
  }
}

async function onUpdateExpectedSignals(values) {
  const messageId = annotatableMessageId.value
  if (messageId == null) return
  try {
    await putMessageExpectedSignals(messageId, values)
    await reloadSignalsLog()
    inspectorRef.value?.refresh()
  } catch {
    // already surfaced via apiFetch
  }
}

// The comment icon's own save handler (see ChatTimeline.vue's
// message-actions slot below) — message-centric like
// onUpdateExpectedState/onUpdateExpectedSignals, but keyed directly off
// the clicked message's own id rather than the current Inspector
// selection: the icon sits on every message row, independent of
// whichever message/transition happens to be selected right now.
async function onSaveComment(messageId, comment) {
  try {
    await putMessageComment(messageId, comment)
    await reloadSignalsLog()
  } catch {
    // already surfaced via apiFetch
  }
}

// Whether this session has anything for "Unlabel all" to actually clear —
// disables the button rather than opening a confirm dialog for nothing.
const hasAnyAnnotations = computed(() => {
  return signalsLog.value.some((s) => s.expected_state != null || s.expected_values != null)
})

const unlabelingAll = ref(false)

async function onUnlabelAll() {
  if (!currentSessionId.value || !hasAnyAnnotations.value) return
  if (!window.confirm('Remove every annotation in this session? This cannot be undone.')) return
  unlabelingAll.value = true
  try {
    await deleteSessionAnnotations(currentSessionId.value)
    await reloadSignalsLog()
    inspectorRef.value?.refresh()
  } catch {
    // already surfaced via apiFetch
  } finally {
    unlabelingAll.value = false
  }
}

// The current session's own persisted "reviewed" flag (see backend
// ChatSession.labeled) — read straight off the Sessions panel's own list
// (its has_annotations field, see chatStore.js's sessions/ChatService.
// _session_payload), the single source of truth for it now, not
// recomputed from signalsLog the way hasAnyAnnotations above still is
// for "Unlabel all" (a genuinely different question: "is there anything
// to clear" vs. "has an expert signed off on this session").
const currentSessionLabeled = computed(() => {
  return sessions.value.find((s) => s.id === currentSessionId.value)?.has_annotations ?? false
})

function handleClose() {
  emit('close')
}

// "Download all" — every session of this project as one .json file (see
// backend tracking/session_export.py), re-uploadable through this same
// view's own Import button (see handleImportSession/importJsonFile).
// Same synthetic-<a> download trick App.vue's own handleModelDownload
// uses for a project's own zip.
const downloadingSessions = ref(false)
async function handleDownloadSessions() {
  downloadingSessions.value = true
  try {
    const blob = await getExportSessions()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${props.projectName}-sessions.json`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch {
    // already surfaced via apiFetch
  } finally {
    downloadingSessions.value = false
  }
}

const markingDone = ref(false)

async function onToggleMarkDone() {
  if (!currentSessionId.value) return
  markingDone.value = true
  try {
    await putSessionLabeled(currentSessionId.value, !currentSessionLabeled.value)
    await refreshSessionsQuietly(true)
  } catch {
    // already surfaced via apiFetch
  } finally {
    markingDone.value = false
  }
}

// The Info tab's own Name/Comment fields (see the #tab-info template
// below) — both save on blur, same "commit on blur, no explicit Save
// button" convention InspectorDetailCard.vue's own title/description
// fields already use — local buffers synced from currentSession on every
// session switch, committed (only if actually changed) on blur.
// refreshSessionsQuietly(true) afterward so the Sessions panel's own row
// (title badge) picks up a rename immediately, same as every other
// session mutation in this file.
const editSessionTitle = ref('')
const editSessionComment = ref('')
watch(currentSession, (session) => {
  editSessionTitle.value = session?.title ?? ''
  editSessionComment.value = session?.comment ?? ''
}, { immediate: true })

// The Info tab's own session card — unified with InspectorSignalsTab.vue/
// InspectorEnvKeysTab.vue's own read-only/editable toggle (click the
// card to open its form, click again to close), rather than always
// showing an editable form the way this card used to. Collapses back
// whenever the selection switches to a different session, same as
// those two components' own expandedSignalName/expandedName resetting
// on a stale identity — editing session A's own name/comment must never
// carry over into session B's own still-open form.
const sessionInfoExpanded = ref(false)
const sessionNameInputRef = ref(null)
watch(currentSessionId, () => { sessionInfoExpanded.value = false })

async function toggleSessionInfo() {
  sessionInfoExpanded.value = !sessionInfoExpanded.value
  if (sessionInfoExpanded.value) {
    await nextTick()
    sessionNameInputRef.value?.focus()
  }
}

async function onUpdateSessionTitle() {
  const sessionId = currentSessionId.value
  if (!sessionId || editSessionTitle.value === (currentSession.value?.title ?? '')) return
  try {
    await putSessionTitle(sessionId, editSessionTitle.value)
    await refreshSessionsQuietly(true)
  } catch {
    // already surfaced via apiFetch
  }
}

async function onUpdateSessionComment() {
  const sessionId = currentSessionId.value
  if (!sessionId || editSessionComment.value === (currentSession.value?.comment ?? '')) return
  try {
    await putSessionComment(sessionId, editSessionComment.value)
    await refreshSessionsQuietly(true)
  } catch {
    // already surfaced via apiFetch
  }
}

// Metrics aren't reactive to props on their own (see
// InspectorMetricsTab.vue's own refresh(active) docstring) — every
// selection change needs an explicit nudge, same as EditProjectView.vue's
// turnCount watcher. No Env tab here (see this view's own tabs, below),
// so no matching nudge.
watch(selected, () => {
  nextTick(() => inspectorRef.value?.refresh())
})

onMounted(() => {
  loadTimeline()
  // The Sessions panel starts open (see benchmarkSessionsPanelOpen) —
  // toggleBenchmarkSessionsPanel only loads on a closed-to-open flip, so
  // the initial open needs its own load.
  loadSessions(true)
  getProjectGraph(props.projectName).then((graph) => {
    autotrackingOnAiMessage.value = graph.autotracking_on_ai_message
  }).catch(() => {
    // already surfaced via apiFetch
  })
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', stopDrag)
  window.addEventListener('resize', handleWindowResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', stopDrag)
  window.removeEventListener('resize', handleWindowResize)
})
</script>

<template>
  <div class="benchmark-overlay">
    <div class="benchmark-header">
      <h2>Label sessions — {{ projectName }}</h2>
      <div class="benchmark-header-actions">
        <button class="close-btn" @click="handleClose">Back</button>
      </div>
    </div>

    <ErrorBanner />

    <div class="benchmark-body">
      <div class="benchmark-chat-pane">
        <div class="sessions-panel-wrap">
          <div class="sessions-panel" :class="{ 'sessions-panel-collapsed': !benchmarkSessionsPanelOpen }" :style="benchmarkSessionsPanelOpen ? { width: sessionsPanelWidth + 'px' } : null">
            <SessionsPanel
              :sessions="sessions"
              :loading="sessionsLoading"
              :current-session-id="currentSessionId"
              :deleting-session-id="deletingSessionId"
              :allow-create="false"
              :allow-delete="false"
              :allow-import="true"
              :allow-download-all="true"
              :downloading-all="downloadingSessions"
              :collapsed="!benchmarkSessionsPanelOpen"
              @update:collapsed="toggleBenchmarkSessionsPanel"
              @select="onSelectSession"
              @import="handleImportSession"
              @download-all="handleDownloadSessions"
            />
          </div>
          <div v-if="benchmarkSessionsPanelOpen" class="split-divider" @mousedown="startSessionsDrag"></div>
        </div>

        <div class="benchmark-chat-content">
          <div class="benchmark-chat-toolbar">
            <span class="benchmark-chat-title">Chat</span>
            <div class="benchmark-chat-toolbar-actions">
              <button
                type="button"
                class="benchmark-unlabel-all-btn"
                :disabled="!hasAnyAnnotations || unlabelingAll"
                @click="onUnlabelAll"
              >
                {{ unlabelingAll ? 'Unlabelling…' : 'Unlabel all' }}
              </button>
              <button
                type="button"
                class="benchmark-mark-done-btn"
                :class="{ 'benchmark-mark-done-btn-active': currentSessionLabeled }"
                :disabled="!currentSessionId || markingDone"
                @click="onToggleMarkDone"
              >
                {{ currentSessionLabeled ? '✓ Done' : 'Mark done' }}
              </button>
            </div>
          </div>

          <p v-if="loading" class="benchmark-status">Loading…</p>
          <p v-else-if="!currentSessionId" class="benchmark-status">
            No session selected — pick one from the Sessions panel first.
          </p>
          <p v-else-if="!timeline.length" class="benchmark-status">This session has no messages yet.</p>

          <ChatTimeline
            v-else
            :timeline="timeline"
            :signals-log="signalsLog"
            :selected="selected"
            :imported="currentSessionIsImported"
            @select-message="selectMessage"
            @select-transition="selectTransition"
          >
            <template #message-actions="{ message }">
              <MessageCommentButton
                :comment="commentForMessage(message, signalsLog)"
                @save="(comment) => onSaveComment(message.id, comment)"
              />
            </template>
          </ChatTimeline>
        </div>
      </div>

      <div class="split-divider inspector-divider" @mousedown="startInspectorDrag"></div>

      <div
        class="benchmark-inspector-panel"
        :class="{ 'benchmark-inspector-panel-collapsed': inspectorCollapsed }"
        :style="inspectorCollapsed ? null : { '--inspector-width': inspectorWidth + 'px' }"
      >
        <Inspector
          ref="inspectorRef"
          :tabs="inspectorTabs"
          v-model:active-tab="inspectorActiveTab"
          v-model:collapsed="inspectorCollapsed"
        >
          <template #tab-states="{ registerTab }">
            <InspectorGraphTab
              :ref="(el) => { registerTab('states')(el); setStatesTabRef(el) }"
              :project-name="projectName"
              :highlighted-state-key="highlightedStateKey"
              :fired-action-edge="firedActionEdge"
              :annotatable="annotatableSignalsRow != null"
              :expected-state="expectedState"
              :imported="currentSessionIsImported"
              @update-expected-state="onUpdateExpectedState"
            />
          </template>
          <template #tab-signals="{ registerTab }">
            <InspectorSignalsTab
              :ref="registerTab('signals')"
              :project-name="projectName"
              :signal-values="signalValues"
              :annotatable="annotatableExpectedSignals"
              :expected-values="expectedValues"
              :state-key="highlightedStateKey"
              :imported="currentSessionIsImported"
              @update-expected-signals="onUpdateExpectedSignals"
            />
          </template>
          <template #tab-metrics="{ registerTab }">
            <InspectorMetricsTab :ref="registerTab('metrics')" :until-message-id="untilMessageId" />
          </template>
          <template #tab-info>
            <div v-if="currentSession" class="benchmark-session-info">
              <div
                class="inspector-signal-block inspector-signal-block-clickable"
                title="Click to open"
                @click="toggleSessionInfo"
              >
                <Transition name="crossfade" mode="out-in">
                  <div v-if="sessionInfoExpanded" key="edit" class="inspector-signal-form">
                    <div class="inspector-signal-header">
                      <span class="inspector-detail-badge inspector-detail-badge-session">Session</span>
                      <input
                        ref="sessionNameInputRef"
                        v-model="editSessionTitle"
                        class="inspector-signal-label-input"
                        placeholder="Untitled session"
                        @click.stop
                        @blur="onUpdateSessionTitle"
                        @keydown.enter.prevent="handleEnterNext"
                      />
                      <CardMenu v-if="currentSessionIsImported">
                        <button type="button" class="card-menu-item-danger" @click="handleDeleteSession(currentSession)">Delete</button>
                      </CardMenu>
                    </div>
                    <span v-if="currentSessionIsImported" class="inspector-detail-badge inspector-detail-badge-neutral">Imported</span>
                    <template v-if="currentSessionHasTimestamps">
                      <label class="inspector-signal-form-label">Started</label>
                      <p class="benchmark-session-info-value">{{ formatSessionTimestamp(currentSession.datetime_start) }}</p>
                      <label class="inspector-signal-form-label">Ended</label>
                      <p class="benchmark-session-info-value">{{ formatSessionTimestamp(currentSession.datetime_end) }}</p>
                    </template>
                    <label class="inspector-signal-form-label">Comment</label>
                    <textarea
                      v-model="editSessionComment"
                      v-autosize
                      class="inspector-signal-textarea"
                      rows="3"
                      placeholder="No comment yet."
                      @click.stop
                      @blur="onUpdateSessionComment"
                    ></textarea>
                  </div>
                  <div v-else key="readonly" class="inspector-signal-readonly">
                    <div class="inspector-signal-header">
                      <span class="inspector-detail-badge inspector-detail-badge-session">Session</span>
                      <span class="inspector-signal-name">{{ currentSession.title || currentSession.end_state || 'Untitled session' }}</span>
                      <CardMenu v-if="currentSessionIsImported">
                        <button type="button" class="card-menu-item-danger" @click="handleDeleteSession(currentSession)">Delete</button>
                      </CardMenu>
                    </div>
                    <span v-if="currentSessionIsImported" class="inspector-detail-badge inspector-detail-badge-neutral">Imported</span>
                    <span v-if="currentSession.comment" class="inspector-signal-ui_description">{{ currentSession.comment }}</span>
                  </div>
                </Transition>
              </div>
              <InspectorDetailCard
                v-if="sessionStartStateKey === sessionEndStateKey"
                :selected-element="startStateElement"
                :closable="false"
                role-badge="Start / End"
              />
              <template v-else>
                <InspectorDetailCard :selected-element="startStateElement" :closable="false" role-badge="Start" />
                <InspectorDetailCard :selected-element="endStateElement" :closable="false" role-badge="End" />
              </template>
            </div>
            <p v-else class="benchmark-session-info-empty">No session selected.</p>
          </template>
        </Inspector>
      </div>
    </div>
  </div>
</template>

<style scoped>
.benchmark-overlay {
  position: fixed;
  inset: 0;
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.benchmark-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #ddd;
}

.benchmark-header h2 {
  margin: 0;
  font-size: 1.1rem;
}

.benchmark-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.close-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.close-btn:hover {
  background: #4a6fa5;
  color: white;
}


.sessions-toggle-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.sessions-toggle-btn:hover {
  background: #eef2f9;
}

.sessions-toggle-btn-on {
  background: #4a6fa5;
  color: white;
}

.sessions-toggle-btn-on:hover {
  background: #3d5c8a;
}

.benchmark-body {
  flex: 1;
  display: flex;
  min-height: 0;
  padding: 1rem;
}

.benchmark-chat-pane {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: row;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

.sessions-panel-wrap {
  display: flex;
  flex-direction: row;
  min-width: 0;
  min-height: 0;
}

.sessions-panel {
  display: flex;
  flex-direction: column;
  flex: none;
  min-height: 0;
  border-right: 1px solid #ddd;
  background: #f9fafb;
  transition: width 0.15s ease;
}

/* Collapsed (see SessionsPanel.vue's own always-visible header toggle) —
   a slim strip, same pattern as ChatWindow.vue's own equivalent. */
.sessions-panel-collapsed {
  width: 2.4rem !important;
}

.benchmark-chat-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.benchmark-chat-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #f5f5f7;
  border-bottom: 1px solid #ddd;
  flex-shrink: 0;
}

/* Same style as Inspector.vue's own .inspector-title. */
.benchmark-chat-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.benchmark-chat-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.benchmark-unlabel-all-btn {
  padding: 0.3rem 0.7rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  cursor: pointer;
  font-size: 0.78rem;
}

.benchmark-unlabel-all-btn:hover:not(:disabled) {
  background: #c62828;
  color: white;
}

.benchmark-unlabel-all-btn:disabled {
  border-color: #ccc;
  color: #ccc;
  cursor: not-allowed;
}

.benchmark-mark-done-btn {
  padding: 0.3rem 0.7rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: white;
  color: #2e7d32;
  cursor: pointer;
  font-size: 0.78rem;
}

.benchmark-mark-done-btn:hover:not(:disabled) {
  background: #2e7d32;
  color: white;
}

.benchmark-mark-done-btn-active {
  background: #2e7d32;
  color: white;
}

.benchmark-mark-done-btn:disabled {
  border-color: #ccc;
  color: #ccc;
  cursor: not-allowed;
}

.benchmark-status {
  margin: auto;
  color: #444;
}

.split-divider {
  flex-shrink: 0;
  width: 6px;
  margin: 0 0.4rem;
  border-radius: 3px;
  background: transparent;
  cursor: col-resize;
}

.split-divider:hover {
  background: #dbe4f0;
}

.benchmark-inspector-panel {
  flex-shrink: 0;
  width: var(--inspector-width);
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* Collapsed (see Inspector.vue's own always-visible header toggle) —
   without this, width stayed pinned to --inspector-width regardless (the
   bug: an empty docked panel that never actually gave its own space back
   to the timeline/sessions split next to it). Same slim-strip convention
   EditProjectView.vue's own .inspector-panel-collapsed uses. */
.benchmark-inspector-panel-collapsed {
  width: 2.4rem !important;
}

.benchmark-session-info {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.benchmark-session-info-label {
  display: block;
  margin-top: 20px;
  font-size: 0.68rem;
  font-weight: 600;
  color: #777;
  text-transform: uppercase;
  letter-spacing: 0.02em;
}

.benchmark-session-info-value {
  margin: 0.15rem 0 0;
  font-size: 0.85rem;
  color: #333;
  word-break: break-word;
}

.benchmark-session-info-empty {
  margin: 0;
  color: #666;
  font-size: 0.85rem;
}

/* The session card itself — unified with InspectorSignalsTab.vue/
   InspectorEnvKeysTab.vue's own read-only/editable block (same classes,
   copied here since Vue's scoped styles never cross component files):
   a badge + title/name row, click to open into an editable form,
   CardMenu for Delete (imported sessions only — see currentSessionIsImported). */
.inspector-signal-block {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.6rem 0.75rem;
  border-radius: 8px;
  border: 1px solid #eee;
  background: #fafafa;
}

.inspector-signal-block-clickable {
  cursor: pointer;
}

.inspector-signal-block-clickable:hover {
  border-color: #c9d6e8;
  background: #f0f4fa;
}

.inspector-signal-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.inspector-detail-badge {
  flex-shrink: 0;
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  color: white;
}

.inspector-detail-badge-session {
  background: #455a64;
}

.inspector-detail-badge-neutral {
  background: #4a6fa5;
}

.inspector-signal-name {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  font-size: 0.85rem;
  color: #333;
}

.inspector-signal-label-input {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  font-size: 0.85rem;
  color: #333;
  border: 1px solid transparent;
  border-radius: 4px;
  padding: 0.1rem 0.3rem;
  background: transparent;
}

.inspector-signal-label-input:hover,
.inspector-signal-label-input:focus {
  border-color: #ccc;
  background: white;
}

.inspector-signal-form-label {
  display: block;
  margin: 20px 0 0.15rem;
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  color: #777;
}

.inspector-signal-textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  font: inherit;
  font-size: 0.78rem;
  line-height: 1.54;
  padding: 0.35rem 0.5rem;
  border-radius: 6px;
  border: 1px solid #ccc;
}

.inspector-signal-ui_description {
  font-size: 0.78rem;
  color: #666;
  line-height: 1.4;
}

.crossfade-enter-active,
.crossfade-leave-active {
  transition: opacity 0.15s ease;
}

.crossfade-enter-from,
.crossfade-leave-to {
  opacity: 0;
}
</style>
