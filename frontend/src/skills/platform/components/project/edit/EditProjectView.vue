<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import ProjectDesignPanel from './design/ProjectDesignPanel.vue'
import RunChat from './run/RunChat.vue'
import ModeSegment from './ModeSegment.vue'
import RevisionMenu from './RevisionMenu.vue'
import Inspector from '../../inspector/Inspector.vue'
import InspectorGraphTab from '../../inspector/InspectorGraphTab.vue'
import InspectorSignalsTab from '../../inspector/InspectorSignalsTab.vue'
import InspectorMetricsTab from '../../inspector/InspectorMetricsTab.vue'
import InspectorEnvTab from '../../inspector/InspectorEnvTab.vue'
import InspectorStateIOTab from '../../inspector/InspectorStateIOTab.vue'
import EditorStateTab from './EditorStateTab.vue'
import ActionsOrderDialog from '../../inspector/ActionsOrderDialog.vue'
import IndexYmlFixesDialog from './IndexYmlFixesDialog.vue'
import SessionDetailCard from '../../../../../components/skillkit/SessionDetailCard.vue'
import ModelMenu from '../../ModelMenu.vue'
import ProfileMenu from '../../../../../components/ProfileMenu.vue'
import AppHeader from '../../../../../components/AppHeader.vue'
import { useLeaveConfirmation } from '../../../useLeaveConfirmation.js'
import { useResizablePanel } from '../../../../../composables/useResizablePanel.js'
import { useProjectFiles } from '../../../useProjectFiles.js'
import { useProjectSources } from '../../../useProjectSources.js'
import { useProjectRevision } from '../../../useProjectRevision.js'
import { useIndexYmlEditing } from '../../../useIndexYmlEditing.js'
import { useProjectCatalog } from '../../../useProjectCatalog.js'
import { useLiveRunTimeline } from '../../../useLiveRunTimeline.js'
import { useStateTabTokens } from '../../../../../composables/useStateTabTokens.js'
import { putSessionTitle, putSessionComment, postModernizeIndexYml } from '../../../api.js'
import { onProjectChanged } from '../../../../../projectChangeEvents.js'
import { projectModes } from '../../../../registry.js'
import { setApiWarning } from '../../../../../errorStore.js'
import { chooseDialog, customDialog } from '../../../../../dialogStore.js'
import { totalTokenBudgetPerSession } from '../../../../../chatStore.js'
import { activeChatMode } from '../../../../../chatSkin.js'
import { setTestProject, releaseTestProject, testStore, testChatModelStore, loadTestChatModels } from '../../../testChatStore.js'

const {
  currentSessionId, turnCount, chatLoading, loadMessages, loadSessions,
  sessions: runSessions, refreshSessionsQuietly,
} = testStore

const props = defineProps({
  projectId: {
    type: String,
    required: true
  },
  profile: { type: Object, default: null },
  buildError: { type: Object, default: null }
})

const emit = defineEmits(['saved', 'renamed', 'back', 'home', 'profile', 'logout'])

const {
  filesLoading, files, currentFileName, justAddedFileName, uploading, creatingFile, deletingFile, renamingFile,
  designPanelRef, codeEditorRef, indexYmlEditorRef, indexCssEditorRef, mdEditorRef,
  currentFileIsMedia, currentFileIsMarkdown, isBehaviorNodeSelected, hasTheme,
  activeEditorIsDirty, activeEditor,
  loadFiles, switchFile, guardedAction, selectFile, jumpToDefinition,
  handleUploadFile, handleNewAttachment, handleNewAspect, handleNewLegal, handleDeleteFile, handleRenameFile,
  handleFileRenamedByHistory, handleFileSaved,
} = useProjectFiles(props.projectId, emit)

const LIVE_CHAT = 'live'
const DESIGN_MODE = { id: 'edit', label: 'Design' }
const RUN_MODE = { id: 'run', label: 'Run', chatMode: 'test' }
const modes = computed(() => [DESIGN_MODE, RUN_MODE, ...projectModes.value])
const modeId = ref(DESIGN_MODE.id)
const mode = computed(() => modeId.value)
const activeMode = computed(() => modes.value.find((entry) => entry.id === modeId.value) ?? DESIGN_MODE)
const editorOpen = computed(() => modeId.value === DESIGN_MODE.id)
const runOpen = computed(() => modeId.value === RUN_MODE.id)

const inspecting = ref(true)
const inspectorRef = ref(null)
const { width: inspectorWidth, startDrag: startInspectorDrag } = useResizablePanel(360, {
  min: 240, max: 560, invert: true, onResize: () => inspectorRef.value?.resize()
})
const selectedGraphElement = ref(null)

const recentlyAddedKey = ref(null)
const RECENTLY_ADDED_FLASH_MS = 1600
let recentlyAddedTimer = null
function flashRecentlyAdded(key) {
  recentlyAddedKey.value = key
  if (recentlyAddedTimer) clearTimeout(recentlyAddedTimer)
  recentlyAddedTimer = setTimeout(() => { recentlyAddedKey.value = null }, RECENTLY_ADDED_FLASH_MS)
}
onBeforeUnmount(() => { if (recentlyAddedTimer) clearTimeout(recentlyAddedTimer) })

const {
  sourcesLoading, sources, currentSourceName, sourcesRootSelected, selectedSource, deletingSource,
  loadSources, selectSource, selectSourcesRoot, handleAddSource, handleAddWebSearchSource, handleUploadSourceFile,
  handleSetSourceField, handleDeleteSource,
} = useProjectSources(props.projectId, guardedAction, flashRecentlyAdded)

async function handleUploadFileOrSource(event) {
  const uploadedFiles = Array.from(event.target.files ?? [])
  const csvFiles = uploadedFiles.filter((file) => /\.csv$/i.test(file.name))
  if (!csvFiles.length) {
    handleUploadFile(event)
    return
  }
  const otherFiles = uploadedFiles.filter((file) => !/\.csv$/i.test(file.name))
  event.target.value = ''
  for (const file of csvFiles) {
    await handleUploadSourceFile(file)
  }
  if (otherFiles.length) handleUploadFile({ target: { files: otherFiles, value: '' } })
}

const sourceContentPanelRef = computed(() => designPanelRef.value?.sourceContentPanelRef ?? null)

async function guardedSourceAction(label, run) {
  if (!sourceContentPanelRef.value?.isDirty) return run()
  const choice = await chooseDialog({
    title: 'Unsaved changes',
    body: `This source's content has unsaved changes. Save before you ${label}?`,
    options: [
      { id: 'save', label: 'Save' },
      { id: 'discard', label: 'Discard' }
    ]
  })
  if (choice === 'save') {
    if (await sourceContentPanelRef.value?.save()) return run()
    return undefined
  }
  if (choice === 'discard') {
    sourceContentPanelRef.value?.discard()
    return run()
  }
  return undefined
}

function selectFileNode(fileName) {
  guardedSourceAction(`switch to "${fileName}"`, () => {
    currentSourceName.value = null
    sourcesRootSelected.value = false
    selectFile(fileName)
  })
}

function selectSourceNode(name) {
  guardedSourceAction(`switch to source "${name}"`, () => {
    selectedGraphElement.value = null
    selectSource(name)
  })
}

function selectSourcesRootNode() {
  guardedSourceAction('view sources', () => {
    selectedGraphElement.value = null
    selectSourcesRoot()
  })
}

const selectedStateKey = computed(() => {
  if (!selectedGraphElement.value) return null
  return selectedGraphElement.value.kind === 'state'
    ? selectedGraphElement.value.data.id
    : selectedGraphElement.value.data.matchStateKey
})

const selectedStateData = computed(() => {
  const element = selectedGraphElement.value
  if (element?.kind === 'state') return element.data
  return indexYmlEditorRef.value?.stateElementFor(selectedStateKey.value)?.data ?? null
})

const runCurrentSession = computed(() => runSessions.value.find((s) => s.id === currentSessionId.value) ?? null)

const {
  validStateKeys, availableStates, buildProblems, stateLabelFor, actionLabelFor, refreshCatalog,
} = useProjectCatalog(props.projectId)

const {
  signalsLog, selected, runChatRef, timeline,
  refreshSignalsLog, refreshSessionStartState, isStateGone,
  selectMessage, selectTransition, highlightedStateKey, firedActionEdge, untilMessageId, envEditable,
  effectiveSignalValues, restartAndPrefill, restartAndResend,
} = useLiveRunTimeline(props.projectId, mode, validStateKeys)

const inspectorTabs = computed(() => {
  if (activeMode.value.inspectorTabs) {
    return activeMode.value.inspectorTabs.map(({ id, label }) => ({ id, label }))
  }
  if (mode.value === 'run') {
    return [
      { id: 'states', label: 'Info' },
      { id: 'signals', label: 'Signals' },
      { id: 'env', label: 'I/O' },
      { id: 'metrics', label: 'Metrics' }
    ]
  }
  if (mode.value === 'edit' && (currentSourceName.value != null || sourcesRootSelected.value || !isBehaviorNodeSelected.value)) {
    return [{ id: 'state', label: 'Info' }]
  }
  return [
    { id: 'state', label: 'Info' },
    { id: 'signals', label: 'Signals' },
    { id: 'output', label: 'I/O' }
  ]
})
const inspectorActiveTab = ref('states')

const { stateTabTokens } = useStateTabTokens(props.projectId, selectedStateKey)

async function ensureDraftChatSession() {
  await loadMessages()
  await loadSessions()
}

function setMode(next) {
  modeId.value = next
  activeChatMode.value = activeMode.value.chatMode ?? LIVE_CHAT
  if (next !== RUN_MODE.id) return
  setTestProject(props.projectId)
  ensureDraftChatSession()
}

onBeforeUnmount(() => {
  activeChatMode.value = 'live'
  releaseTestProject()
})

const { width: explorerWidth, startDrag: startExplorerDrag } = useResizablePanel(220, { min: 160, max: 420 })

const workspace = reactive({
  projectId: props.projectId,
  availableStates,
  files,
  highlightedStateKey,
  recentlyAddedKey,
  stateElementFor: (key) => (key == null ? null : (indexYmlEditorRef.value?.stateElementFor(key) ?? null)),
  selectElement: handleTabSelect,
  selectAttachment: selectFile,
  jumpToAttachment: handleJumpToAttachment,
})

function resyncSelectedGraphElement() {
  const el = selectedGraphElement.value
  if (!el) return
  if (el.kind === 'state') {
    selectedGraphElement.value = indexYmlEditorRef.value?.stateElementFor(el.data.id) ?? null
  } else if (el.kind === 'action') {
    const actions = indexYmlEditorRef.value?.actionsForState(el.data.matchStateKey) ?? []
    selectedGraphElement.value = actions.find((a) => a.data.actionName === el.data.actionName) ?? null
  }
}

async function refreshAfterProjectEdit() {
  await indexYmlEditorRef.value?.refresh(false)
  await indexYmlEditorRef.value?.reloadCode()
  if (runOpen.value && !chatLoading.value) await ensureDraftChatSession()
  if (inspecting.value) await inspectorRef.value?.refresh()
  resyncSelectedGraphElement()
}

const unsubscribeProjectChanged = onProjectChanged((changedProjectId) => {
  if (changedProjectId === props.projectId) return refreshAfterProjectEdit()
})
onBeforeUnmount(unsubscribeProjectChanged)

const {
  projectRevision, reverting, refreshProjectRevision,
  canRevert, revisionMenuOpen, handleRevert,
} = useProjectRevision(props.projectId, currentFileName, activeEditor, selectedGraphElement)

const {
  handleAddState, handleAddSignal, handleAddEnvKey, handleAddAction,
  handleSetStateField, handleSetProjectField, handleSetServiceLevel, handleSetActionField, handleSetSignalField, handleSetEnvKeyField,
  handleDeleteState, handleDeleteAction, handleDeleteSignal, handleDeleteEnvKey,
} = useIndexYmlEditing(
  props.projectId, guardedAction, indexYmlEditorRef, jumpToDefinition, selectedGraphElement, selectedStateKey, flashRecentlyAdded
)

function jumpSilently(target) {
  jumpToDefinition(target, { silent: true })
}

function handleTabSelect(element) {
  selectedGraphElement.value = element
  if (!element) return
  if (element.kind === 'state') jumpSilently({ kind: 'state', stateKey: element.data.id })
  else jumpSilently({ kind: 'action', stateKey: element.data.matchStateKey, actionName: element.data.actionName })
}

function followProjectRename(result) {
  const renamedTo = result?.project_id
  if (renamedTo && renamedTo !== props.projectId) emit('renamed', renamedTo)
}

async function handleProjectFieldSet(field, value) {
  followProjectRename(await handleSetProjectField(field, value))
}

function handleProjectFileSaved(result) {
  handleFileSaved(result)
  followProjectRename(result)
}

function handleSetSelectedElementField(field, value) {
  const element = selectedGraphElement.value
  if (!element) return undefined
  if (element.kind === 'state') return handleSetStateField(element.data.id, field, value)
  return handleSetActionField(element.data.matchStateKey, element.data.actionName, field, value)
}

function handleDeleteSelectedElement(element) {
  if (!element) return
  if (element.kind === 'state') handleDeleteState(element.data.id)
  else handleDeleteAction(element.data.matchStateKey, element.data.actionName)
}

function handleOpenActionsOrder(element) {
  if (element?.kind !== 'state') return
  const stateKey = element.data.id
  guardedAction('reorder actions', () => {
    customDialog({
      component: ActionsOrderDialog,
      props: {
        projectId: props.projectId,
        stateName: stateKey,
        actions: indexYmlEditorRef.value?.actionsForState(stateKey) ?? []
      }
    })
  })
}

function handleJumpToAttachment(fileName) {
  const stateKey = selectedGraphElement.value?.data.id
  if (stateKey == null) return
  jumpSilently({ kind: 'attachment', stateKey, fileName })
}

const { confirmLeaveIfNeeded } = useLeaveConfirmation(activeEditorIsDirty, 'Discard unsaved changes to this file?')

async function leaveEditProject(onLeave) {
  if (!(await confirmLeaveIfNeeded())) return
  onLeave()
}

function handleBack() {
  leaveEditProject(() => emit('back'))
}

async function openInspect() {
  await nextTick()
  await refreshSignalsLog()
}

function handleInspectorCollapsedChange(collapsed) {
  inspecting.value = !collapsed
  if (inspecting.value) openInspect()
}

function handleWindowResize() {
  inspectorRef.value?.resize()
}

watch(turnCount, () => {
  selected.value = null
  refreshSignalsLog()
  if (!inspecting.value) return
  nextTick(() => {
    inspectorRef.value?.refresh()
    if (editorOpen.value) indexYmlEditorRef.value?.refresh(false)
  })
})

watch(selected, () => {
  if (!inspecting.value) return
  nextTick(() => {
    inspectorRef.value?.refresh()
  })
})

watch(currentSessionId, () => {
  selected.value = null
  refreshSessionStartState()
  refreshSignalsLog()
  if (inspecting.value) nextTick(() => inspectorRef.value?.refresh())
})

function reportPause() {
  const info = projectRevision.value
  if (!info?.is_paused) return
  if (info.broken?.draft) return
  if (info.broken?.published) {
    setApiWarning(
      `Project '${props.projectId}' is paused: its published revision ${info.published_revision} no longer builds.`
        + ' This draft does — publish it to bring the project back.'
    )
    return
  }
  setApiWarning(info.paused_reason || `Project '${props.projectId}' is currently paused.`)
}

function modernizeIndexYml() {
  return postModernizeIndexYml(props.projectId)
    .then((result) => result.fixed)
    .catch(() => [])
}

onMounted(async () => {
  const fixed = await modernizeIndexYml()
  loadFiles()
  loadSources()
  loadTestChatModels()
  refreshSessionStartState()
  refreshSignalsLog()
  refreshCatalog()
  await refreshProjectRevision()
  reportPause()
  if (inspecting.value) openInspect()
  window.addEventListener('resize', handleWindowResize)
  if (props.buildError?.file === 'index.yml') {
    await nextTick()
    indexYmlEditorRef.value?.showBuildError(props.buildError.line)
  }
  if (fixed.length) await customDialog({ component: IndexYmlFixesDialog, props: { fixes: fixed } })
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', handleWindowResize)
})

async function showProblem(problem) {
  if (problem.line == null) return
  modeId.value = DESIGN_MODE.id
  await nextTick()
  indexYmlEditorRef.value?.showBuildError(problem.line)
}

async function handleSetSessionTitle(sessionId, title) {
  await putSessionTitle(sessionId, title)
  await refreshSessionsQuietly()
}

async function handleSetSessionComment(sessionId, comment) {
  await putSessionComment(sessionId, comment)
  await refreshSessionsQuietly()
}
</script>

<template>
  <div class="edit-project-overlay">
    <AppHeader>
      <template #left>
        <button class="app-header-icon-btn" title="Back" @click="handleBack">«</button>
        <ModelMenu :model-store="testChatModelStore" />
      </template>
      <template #center>
        <ModeSegment :mode="modeId" :modes="modes" @update:mode="setMode" />
      </template>
      <template #right>
        <div class="edit-project-header-actions">
          <RevisionMenu
            v-if="projectRevision"
            :project-revision="projectRevision"
            :reverting="reverting"
            :can-revert="canRevert"
            v-model:menu-open="revisionMenuOpen"
            @revert="handleRevert"
          />
          <ProfileMenu :profile="profile" @home="emit('home')" @profile="emit('profile')" @logout="emit('logout')" />
        </div>
      </template>
    </AppHeader>

    <div v-if="buildProblems.length" class="build-warnings-banner build-problems-banner">
      <button
        v-for="(problem, index) in buildProblems"
        :key="index"
        type="button"
        class="build-warnings-banner-line build-problems-banner-line"
        :class="{ 'build-warnings-banner-line-locatable': problem.line != null }"
        :disabled="problem.line == null"
        :title="problem.line == null ? '' : `Go to ${problem.section ?? 'index.yml'}`"
        @click="showProblem(problem)"
      >{{ problem.message }}</button>
    </div>

    <div class="edit-project-body">
      <div class="edit-project-panels">
        <ProjectDesignPanel
          v-show="editorOpen"
          ref="designPanelRef"
          :project-id="projectId"
          :files="files"
          :files-loading="filesLoading"
          :current-file-name="currentFileName"
          :just-added-file-name="justAddedFileName"
          :uploading="uploading"
          :creating-file="creatingFile"
          :explorer-width="explorerWidth"
          :current-file-is-media="currentFileIsMedia"
          :current-file-is-markdown="currentFileIsMarkdown"
          :highlighted-state-key="highlightedStateKey"
          :fired-action-edge="firedActionEdge"
          :selected-element="selectedGraphElement"
          :sources="sources"
          :sources-loading="sourcesLoading"
          :current-source-name="currentSourceName"
          :sources-root-selected="sourcesRootSelected"
          :modified-files="projectRevision?.modified_files ?? []"
          :current-revision="projectRevision?.revision ?? null"
          @start-explorer-drag="startExplorerDrag"
          @new-attachment="handleNewAttachment"
          @new-aspect="handleNewAspect"
          @new-legal="handleNewLegal"
          @new-source="handleAddSource"
          @new-websearch-source="handleAddWebSearchSource"
          @select-file="selectFileNode"
          @select-source="selectSourceNode"
          @select-sources-root="selectSourcesRootNode"
          @upload-file="handleUploadFileOrSource"
          @jump-to-definition="jumpSilently"
          @select="selectedGraphElement = $event"
          @saved="handleProjectFileSaved"
          @renamed="handleFileRenamedByHistory"
        />

        <RunChat
          v-if="runOpen"
          ref="runChatRef"
          :timeline="timeline"
          :signals-log="signalsLog"
          :selected="selected"
          :has-theme="hasTheme"
          :resolve-state-label="stateLabelFor"
          :resolve-action-label="actionLabelFor"
          :is-state-gone="isStateGone"
          @select-message="selectMessage"
          @select-transition="selectTransition"
          @restart-prefill="restartAndPrefill"
          @restart-resend="restartAndResend"
        />

        <component v-if="activeMode.panel" :is="activeMode.panel" :workspace="workspace" />
      </div>

      <div class="inspector-wrap">
        <div v-if="inspecting" class="split-divider inspector-divider" @mousedown="startInspectorDrag"></div>

        <div class="inspector-panel" :class="{ 'inspector-panel-collapsed': !inspecting }" :style="inspecting ? { '--inspector-width': inspectorWidth + 'px' } : null">
          <Inspector
            ref="inspectorRef"
            :tabs="inspectorTabs"
            v-model:active-tab="inspectorActiveTab"
            :collapsed="!inspecting"
            @update:collapsed="handleInspectorCollapsedChange"
          >
            <template #tab-states="{ registerTab }">
              <SessionDetailCard
                v-if="runCurrentSession"
                :session="runCurrentSession"
                @set-title="handleSetSessionTitle"
                @set-comment="handleSetSessionComment"
              />
              <InspectorGraphTab
                :ref="registerTab('states')"
                :project-id="projectId"
                :highlighted-state-key="highlightedStateKey"
                :auto-jump-on-highlight-change="true"
                :fired-action-edge="firedActionEdge"
                :editable-files="files"
                :sources="sources"
                :editable="true"
                :available-states="availableStates"
                :recently-added-key="recentlyAddedKey"
                :state-tokens="stateTabTokens"
                :save-field="handleSetSelectedElementField"
                @jump-to-definition="jumpToDefinition"
                @select-attachment="selectFile"
                @jump-to-attachment="handleJumpToAttachment"
                @select="handleTabSelect"
                @set-field="handleSetSelectedElementField"
                @delete="handleDeleteSelectedElement"
                @open-actions-order="handleOpenActionsOrder"
              />
            </template>
            <template #tab-state="{ registerTab }">
              <EditorStateTab
                :ref="registerTab('state')"
                :project-id="projectId"
                :selected-element="selectedGraphElement"
                :state-tokens="stateTabTokens"
                :fired-action-edge="firedActionEdge"
                :available-states="availableStates"
                                :total-token-budget-per-session="totalTokenBudgetPerSession"
                                :editable-files="files"
                :highlighted-state-key="highlightedStateKey"
                :recently-added-key="recentlyAddedKey"
                :sources="sources"
                :current-file-name="editorOpen ? currentFileName : null"
                :deleting-file="deletingFile"
                :renaming-file="renamingFile"
                :selected-source="editorOpen ? selectedSource : null"
                :deleting-source="deletingSource"
                :sources-root-selected="editorOpen && sourcesRootSelected"
                @select="handleTabSelect"
                @select-attachment="selectFile"
                @jump-to-attachment="handleJumpToAttachment"
                @set-field="handleSetSelectedElementField"
                :save-field="handleSetSelectedElementField"
                @set-project-field="handleProjectFieldSet"
                @set-service-level="handleSetServiceLevel"
                @delete="handleDeleteSelectedElement"
                @open-actions-order="handleOpenActionsOrder"
                @add-state="handleAddState"
                @add-action="handleAddAction"
                @delete-file="handleDeleteFile"
                @rename-file="handleRenameFile"
                @set-source-field="handleSetSourceField"
                @delete-source="(source) => handleDeleteSource(source.name)"
              />
            </template>
            <template
              v-for="tab in activeMode.inspectorTabs"
              #[`tab-${tab.id}`]="{ registerTab }"
              :key="tab.id"
            >
              <component :is="tab.component" :ref="registerTab(tab.id)" :workspace="workspace" />
            </template>
            <template #tab-signals="{ registerTab }">
              <InspectorSignalsTab
                :ref="registerTab('signals')"
                :project-id="projectId"
                :signal-values="effectiveSignalValues"
                :editable-files="files"
                :state-key="editorOpen ? selectedStateKey : highlightedStateKey"
                :recently-added-key="recentlyAddedKey"
                @jump-to-definition="jumpSilently"
                @select-attachment="selectFile"
                @set-field="handleSetSignalField"
                @add-signal="handleAddSignal"
                @delete="handleDeleteSignal"
              />
            </template>
            <template #tab-metrics="{ registerTab }">
              <InspectorMetricsTab :ref="registerTab('metrics')" :until-message-id="untilMessageId" :project-id="projectId" />
            </template>
            <template #tab-env="{ registerTab }">
              <InspectorEnvTab
                :ref="registerTab('env')"
                :session-id="currentSessionId"
                :until-message-id="untilMessageId"
                :editable="envEditable"
              />
            </template>
            <template #tab-output="{ registerTab }">
              <InspectorStateIOTab
                :ref="registerTab('output')"
                :project-id="projectId"
                :state-key="selectedStateKey"
                :state-data="selectedStateData"
                :recently-added-key="recentlyAddedKey"
                :save-field="(field, value) => handleSetStateField(selectedStateKey, field, value)"
                @set-field="(field, value) => handleSetStateField(selectedStateKey, field, value)"
                @set-env-key-field="handleSetEnvKeyField"
                @add-env-key="handleAddEnvKey"
                @delete-env-key="handleDeleteEnvKey"
                @jump-to-definition="jumpSilently"
              />
            </template>
          </Inspector>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.build-warnings-banner {
  padding: 0.5rem 1rem;
  background: #fff4e0;
  border-bottom: 1px solid #f0d9a8;
  flex: none;
}

.build-warnings-banner-line {
  display: block;
  width: 100%;
  margin: 0;
  padding: 0;
  border: none;
  background: none;
  color: #b06a00;
  font-size: 0.85rem;
  font-family: inherit;
  text-align: left;
}

.build-problems-banner {
  background: #fdecea;
  border-bottom-color: #f5c2bd;
}

.build-problems-banner-line {
  color: #b3261e;
}

.build-warnings-banner-line-locatable {
  cursor: pointer;
  text-decoration: underline;
  text-decoration-style: dotted;
  text-underline-offset: 0.15rem;
}

.build-warnings-banner-line + .build-warnings-banner-line {
  margin-top: 0.25rem;
}

.edit-project-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
  box-sizing: border-box;
  padding-left: var(--safe-area-left);
  padding-right: var(--safe-area-right);
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.edit-project-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.edit-project-body {
  flex: 1;
  display: flex;
  min-height: 0;
  padding: 1rem;
}

.edit-project-panels {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
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

@media (max-width: 899.98px) {
  .inspector-divider {
    display: none;
  }
}

.inspector-wrap {
  display: flex;
  flex-direction: row;
  min-height: 0;
}

.inspector-panel {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
  box-sizing: border-box;
  padding-top: var(--safe-area-top);
  padding-left: var(--safe-area-left);
  padding-right: var(--safe-area-right);
  background: white;
  z-index: 150;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

@media (min-width: 900px) {
  .inspector-panel {
    position: static;
    inset: auto;
    padding: 0;
    z-index: auto;
    flex-shrink: 0;
    width: var(--inspector-width);
    border: 1px solid #ddd;
    border-radius: 8px;
    overflow: hidden;
    transition: width 0.15s ease;
  }
}

.inspector-panel-collapsed {
  position: static !important;
  inset: auto !important;
  padding: 0 !important;
  z-index: auto !important;
  flex-shrink: 0;
  width: 2.4rem !important;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}
</style>
