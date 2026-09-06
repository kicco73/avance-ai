<script setup>
// Composes the three mode panels (Design/Run/Test) and owns only what's
// cross-cutting: mode switch, header/publish controls, the Inspector.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ProjectDesignPanel from './design/ProjectDesignPanel.vue'
import RunChat from './run/RunChat.vue'
import ProjectTestPanel from './test/ProjectTestPanel.vue'
import ModeSegment from './ModeSegment.vue'
import PublishControls from './PublishControls.vue'
import PublishRemapDialog from './PublishRemapDialog.vue'
import Inspector from '../../inspector/Inspector.vue'
import InspectorGraphTab from '../../inspector/InspectorGraphTab.vue'
import InspectorSignalsTab from '../../inspector/InspectorSignalsTab.vue'
import InspectorMetricsTab from '../../inspector/InspectorMetricsTab.vue'
import InspectorEnvTab from '../../inspector/InspectorEnvTab.vue'
import InspectorEnvKeysTab from '../../inspector/InspectorEnvKeysTab.vue'
import InspectorStateTab from '../../inspector/InspectorStateTab.vue'
import ActionsOrderDialog from '../../inspector/ActionsOrderDialog.vue'
import SessionDetailCard from '../../inspector/SessionDetailCard.vue'
import InspectorUserInfoCard from '../../inspector/InspectorUserInfoCard.vue'
import InspectorSignalDetailCard from '../../inspector/InspectorSignalDetailCard.vue'
import ModelMenu from '../../ModelMenu.vue'
import ProfileMenu from '../../ProfileMenu.vue'
import AppHeader from '../../AppHeader.vue'
import { useLeaveConfirmation } from '../../../composables/useLeaveConfirmation.js'
import { useResizablePanel } from '../../../composables/useResizablePanel.js'
import { useProjectFiles } from '../../../composables/useProjectFiles.js'
import { useProjectSources } from '../../../composables/useProjectSources.js'
import { useProjectPublishing } from '../../../composables/useProjectPublishing.js'
import { useIndexYmlEditing } from '../../../composables/useIndexYmlEditing.js'
import { useProjectCatalog } from '../../../composables/useProjectCatalog.js'
import { useLiveRunTimeline } from '../../../composables/useLiveRunTimeline.js'
import { useTestModeSelection } from '../../../composables/useTestModeSelection.js'
import { useStateTabTokens } from '../../../composables/useStateTabTokens.js'
import { onProjectChanged } from '../../../projectChangeEvents.js'
import { clearProjectHistory } from '../../../api.js'
import { setApiWarning } from '../../../errorStore.js'
import { chooseDialog, customDialog } from '../../../dialogStore.js'
import { totalTokenBudgetPerSession } from '../../../chatStore.js'
import { activeChatMode } from '../../../chatSkin.js'
import { setTestProject, testStore, testChatModelStore, loadTestChatModels } from '../../../testChatStore.js'

// `runSessions` is the "Run" tab's own draft session pool, unrelated to
// chatStore's project-wide `sessions` catalog used by useTestModeSelection.
const { currentSessionId, turnCount, loadMessages, loadSessions, sessions: runSessions, refreshSessionsQuietly } = testStore

const props = defineProps({
  projectId: {
    type: String,
    required: true
  },
  profile: { type: Object, default: null }
})

setTestProject(props.projectId)

const emit = defineEmits(['saved', 'back', 'home', 'profile', 'logout'])

const {
  filesLoading, files, currentFileName, justAddedFileName, uploading, creatingFile, deletingFile, renamingFile,
  designPanelRef, codeEditorRef, indexYmlEditorRef, indexCssEditorRef, mdEditorRef,
  currentFileIsImage, currentFileIsMarkdown, isBehaviorNodeSelected, hasTheme,
  activeEditorIsDirty, activeEditor,
  loadFiles, switchFile, guardedAction, selectFile, jumpToDefinition,
  handleUploadFile, handleNewAttachment, handleNewAspect, handleNewLegal, handleDeleteFile, handleRenameFile,
  handleFileRenamedByHistory, handleFileSaved,
} = useProjectFiles(props.projectId, emit)

const mode = ref('edit')
const editorOpen = computed(() => mode.value === 'edit')
const runOpen = computed(() => mode.value === 'run')
const testOpen = computed(() => mode.value === 'test')

const inspecting = ref(true)
const inspectorRef = ref(null)
const { width: inspectorWidth, startDrag: startInspectorDrag } = useResizablePanel(360, {
  min: 240, max: 560, invert: true, onResize: () => inspectorRef.value?.resize()
})
// {kind, data} | null — shared by the Graph and the Inspector's Info tab.
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
  loadSources, selectSource, selectSourcesRoot, handleAddSource, handleUploadSourceFile, handleSetSourceField, handleDeleteSource,
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

const runCurrentSession = computed(() => runSessions.value.find((s) => s.id === currentSessionId.value) ?? null)

const {
  handleAutoSelect, autoSelectedSession, autoSelectedStateKey, autoSelectedElement, autoSelectedUser,
  autoSelectedSignalName, autoSelectedSignal, autoSessionInputTokens, autoSessionStartElement, autoSessionEndElement,
  ensureUsersList, ensureSignalsList,
} = useTestModeSelection(props.projectId, indexYmlEditorRef)

const {
  validStateKeys, availableStates, availableEnvKeys, buildWarnings, stateLabelFor, actionLabelFor, refreshCatalog,
} = useProjectCatalog(props.projectId)

const {
  signalsLog, selected, runChatRef, timeline,
  refreshSignalsLog, refreshSessionStartState, refreshSignalValues, isStateGone,
  selectMessage, selectTransition, highlightedStateKey, firedActionEdge, untilMessageId, envEditable,
  effectiveSignalValues, restartAndPrefill, restartAndResend,
} = useLiveRunTimeline(props.projectId, mode, validStateKeys)

const inspectorTabs = computed(() => {
  if (mode.value === 'run') {
    return [
      { id: 'states', label: 'Info' },
      { id: 'signals', label: 'Signals' },
      { id: 'metrics', label: 'Metrics' },
      { id: 'env', label: 'Env' }
    ]
  }
  if (mode.value === 'test') {
    return [{ id: 'state', label: 'Info' }, { id: 'user', label: 'User' }]
  }
  if (mode.value === 'edit' && (currentSourceName.value != null || sourcesRootSelected.value || !isBehaviorNodeSelected.value)) {
    return [{ id: 'state', label: 'Info' }]
  }
  return [
    { id: 'state', label: 'Info' },
    { id: 'signals', label: 'Signals' },
    { id: 'env-keys', label: 'Env' }
  ]
})
const inspectorActiveTab = ref('states')

const stateTabTokensKey = computed(() => (mode.value === 'test' ? autoSelectedStateKey.value : selectedStateKey.value))
const { stateTabTokens, refreshStateTabTokens } = useStateTabTokens(props.projectId, stateTabTokensKey)

async function ensureDraftChatSession() {
  await loadMessages()
  await loadSessions()
}

function setMode(next) {
  mode.value = next
  activeChatMode.value = next === 'run' ? 'test' : 'live'
  if (next === 'run') ensureDraftChatSession()
  if (next === 'test') {
    ensureUsersList()
    ensureSignalsList()
  }
}

onBeforeUnmount(() => { activeChatMode.value = 'live' })

const { width: explorerWidth, startDrag: startExplorerDrag } = useResizablePanel(220, { min: 160, max: 420 })

async function refreshAfterProjectEdit() {
  await indexYmlEditorRef.value?.refresh(false)
  await indexYmlEditorRef.value?.reloadCode()
  if (inspecting.value) await inspectorRef.value?.refresh()
  refreshCatalog()
  refreshProjectRevision()
  refreshStateTabTokens()
}

const unsubscribeProjectChanged = onProjectChanged((changedProjectId) => {
  if (changedProjectId === props.projectId) return refreshAfterProjectEdit()
})
onBeforeUnmount(unsubscribeProjectChanged)

const {
  projectRevision, publishing, publishRemapPrompt, pendingLeaveAction,
  refreshProjectRevision, publishUpToDate,
  handlePublish, confirmPublishRemap, cancelPublishRemap,
  canRevert, publishMenuOpen, handleRevert,
} = useProjectPublishing(props.projectId, currentFileName, activeEditor, selectedGraphElement)

const {
  handleAddState, handleAddSignal, handleAddEnvKey, handleAddAction,
  handleSetStateField, handleSetProjectField, handleSetActionField, handleSetSignalField, handleSetEnvKeyField,
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
  if (publishUpToDate.value) {
    onLeave()
    return
  }
  const choice = await chooseDialog({
    title: 'Unpublished changes',
    body: `Revision ${projectRevision.value?.revision} isn't published yet.`,
    options: [
      { id: 'publish', label: 'Publish and close' },
      { id: 'leave', label: 'Leave pending' }
    ]
  })
  if (choice === 'publish') {
    pendingLeaveAction.value = onLeave
    handlePublish()
    return
  }
  if (choice === 'leave') onLeave()
}

function handleBack() {
  leaveEditProject(() => emit('back'))
}

async function openInspect() {
  await nextTick()
  await refreshSignalValues()
}

function handleInspectorCollapsedChange(collapsed) {
  inspecting.value = !collapsed
  if (inspecting.value) openInspect()
}

function handleWindowResize() {
  inspectorRef.value?.resize()
}

// A completed turn: follow the newest message again, refresh what a turn can change.
watch(turnCount, () => {
  selected.value = null
  refreshSignalsLog()
  if (!inspecting.value) return
  refreshSignalValues()
  inspectorRef.value?.refresh()
  if (editorOpen.value) indexYmlEditorRef.value?.refresh(false)
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

// Gates mounting the editors until clearProjectHistory has finished.
const historyCleared = ref(false)

onMounted(async () => {
  loadFiles()
  loadSources()
  loadTestChatModels()
  refreshSessionStartState()
  refreshSignalsLog()
  refreshCatalog()
  await refreshProjectRevision()
  if (projectRevision.value?.is_paused) {
    setApiWarning(projectRevision.value.paused_reason || `Project '${props.projectId}' is currently paused.`)
  }
  if (inspecting.value) openInspect()
  window.addEventListener('resize', handleWindowResize)
  try {
    await clearProjectHistory(props.projectId)
  } catch {
    // already surfaced via apiFetch
  } finally {
    historyCleared.value = true
  }
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', handleWindowResize)
})
</script>

<template>
  <div class="edit-project-overlay">
    <AppHeader>
      <template #left>
        <button class="app-header-icon-btn" title="Back" @click="handleBack">«</button>
        <ModelMenu :model-store="testChatModelStore" />
      </template>
      <template #center>
        <ModeSegment :mode="mode" @update:mode="setMode" />
      </template>
      <template #right>
        <div class="edit-project-header-actions">
          <PublishControls
            v-if="projectRevision"
            :project-revision="projectRevision"
            :publishing="publishing"
            :publish-up-to-date="publishUpToDate"
            :can-revert="canRevert"
            v-model:menu-open="publishMenuOpen"
            @publish="handlePublish"
            @revert="handleRevert"
          />
          <ProfileMenu :profile="profile" @home="emit('home')" @profile="emit('profile')" @logout="emit('logout')" />
        </div>
      </template>
    </AppHeader>

    <div v-if="buildWarnings.length" class="build-warnings-banner">
      <p v-for="(warning, index) in buildWarnings" :key="index" class="build-warnings-banner-line">{{ warning }}</p>
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
          :history-cleared="historyCleared"
          :current-file-is-image="currentFileIsImage"
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
          @new-env-source="() => handleAddSource('env')"
          @select-file="selectFileNode"
          @select-source="selectSourceNode"
          @select-sources-root="selectSourcesRootNode"
          @upload-file="handleUploadFileOrSource"
          @jump-to-definition="jumpSilently"
          @select="selectedGraphElement = $event"
          @saved="handleFileSaved"
          @renamed="handleFileRenamedByHistory"
          @switch-to-env-keys="inspectorActiveTab = 'env-keys'"
        />

        <Transition name="panel-slide-bottom">
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
        </Transition>

        <ProjectTestPanel v-if="testOpen" :project-id="projectId" @select="handleAutoSelect" />
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
                @updated="refreshSessionsQuietly"
              />
              <InspectorGraphTab
                :ref="registerTab('states')"
                :project-id="projectId"
                :highlighted-state-key="highlightedStateKey"
                :auto-jump-on-highlight-change="true"
                :fired-action-edge="firedActionEdge"
                :editable-files="files"
                @jump-to-definition="jumpToDefinition"
                @select-attachment="selectFile"
              />
            </template>
            <template #tab-state="{ registerTab }">
              <InspectorSignalDetailCard
                v-if="mode === 'test' && autoSelectedSignalName != null"
                :ref="registerTab('state')"
                :signal="autoSelectedSignal"
              />
              <InspectorStateTab
                v-else
                :ref="registerTab('state')"
                :project-id="projectId"
                :selected-element="mode === 'test' ? autoSelectedElement : selectedGraphElement"
                :state-tokens="stateTabTokens"
                :fired-action-edge="firedActionEdge"
                :available-states="availableStates"
                :available-env-keys="availableEnvKeys"
                :selected-session="mode === 'test' ? autoSelectedSession : null"
                :session-input-tokens="mode === 'test' ? autoSessionInputTokens : null"
                :total-token-budget-per-session="totalTokenBudgetPerSession"
                :session-start-element="mode === 'test' ? autoSessionStartElement : null"
                :session-end-element="mode === 'test' ? autoSessionEndElement : null"
                :read-only="mode === 'test'"
                :editable-files="files"
                :highlighted-state-key="highlightedStateKey"
                :recently-added-key="recentlyAddedKey"
                :current-file-name="mode === 'edit' ? currentFileName : null"
                :deleting-file="deletingFile"
                :renaming-file="renamingFile"
                :selected-source="mode === 'edit' ? selectedSource : null"
                :deleting-source="deletingSource"
                :sources-root-selected="mode === 'edit' && sourcesRootSelected"
                @select="handleTabSelect"
                @select-attachment="selectFile"
                @jump-to-attachment="handleJumpToAttachment"
                @set-field="handleSetSelectedElementField"
                :save-field="handleSetSelectedElementField"
                @set-project-field="handleSetProjectField"
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
            <template #tab-user="{ registerTab }">
              <InspectorUserInfoCard :ref="registerTab('user')" :user="autoSelectedUser" />
            </template>
            <template #tab-signals="{ registerTab }">
              <InspectorSignalsTab
                :ref="registerTab('signals')"
                :project-id="projectId"
                :signal-values="effectiveSignalValues"
                :editable-files="mode === 'edit' ? files : null"
                :state-key="mode === 'edit' ? selectedStateKey : highlightedStateKey"
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
            <template #tab-env-keys="{ registerTab }">
              <InspectorEnvKeysTab
                :ref="registerTab('env-keys')"
                :project-id="projectId"
                :recently-added-key="recentlyAddedKey"
                @jump-to-definition="jumpSilently"
                @set-field="handleSetEnvKeyField"
                @add-env-key="handleAddEnvKey"
                @delete="handleDeleteEnvKey"
              />
            </template>
          </Inspector>
        </div>
      </div>
    </div>

    <PublishRemapDialog
      v-if="publishRemapPrompt"
      :prompt="publishRemapPrompt"
      :publishing="publishing"
      @confirm="confirmPublishRemap"
      @cancel="cancelPublishRemap"
    />
  </div>
</template>

<style scoped>
/* Same amber palette as ErrorBanner.vue's -warning variant, kept inline
   since build_warnings are a standing property of the draft. */
.build-warnings-banner {
  padding: 0.5rem 1rem;
  background: #fff4e0;
  border-bottom: 1px solid #f0d9a8;
  flex: none;
}

.build-warnings-banner-line {
  margin: 0;
  color: #b06a00;
  font-size: 0.85rem;
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
  position: relative;
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.panel-slide-bottom-enter-active,
.panel-slide-bottom-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
  position: absolute;
  inset: 0;
}

.panel-slide-bottom-leave-active {
  z-index: -1;
}

.panel-slide-bottom-enter-from,
.panel-slide-bottom-leave-to {
  opacity: 0;
  transform: translateY(16px);
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

/* Narrow screens: the inspector takes over the whole overlay. */
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
