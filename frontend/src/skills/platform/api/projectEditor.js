import { apiFetch, projectFetch } from '../../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function getProjectGraph(projectId, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/graph${query}`)
}


export function getProjectEnvKeys(projectId, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/env-keys${query}`)
}

export function getProjectMetadata(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/project`)
}

export function getProjectSources(projectId, sessionId) {
  const query = sessionId != null ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sources${query}`)
}

export function postCreateInvite(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/invites`, { method: 'POST' })
}



export function putProjectField(projectId, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/project/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function postModernizeIndexYml(projectId) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/index-yml/modernize`,
    { method: 'POST' }
  )
}

export function putServiceLevel(projectId, service, level) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/services/${encodeURIComponent(service)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ level }) }
  )
}



export function getProjectFiles(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files`)
}


export function getProjectFile(projectId, fileName) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}`)
}

export function putProjectFile(projectId, fileName, content) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    body: content
  })
}

export function renameProjectFile(projectId, fileName, newName) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}/rename`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_name: newName })
  })
}

export function postSourceWebImport(projectId, sourceName, query, onProgress) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceName)}/web-import`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query }) },
    { parse: 'sse', onProgress }
  )
}

export function putProjectFileBinary(projectId, fileName, file) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': file.type },
    body: file
  })
}


export function undoProjectFile(projectId, fileName, content) {
  return apiFetch(
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}/undo`,
    { method: 'POST', headers: { 'Content-Type': 'text/plain; charset=utf-8' }, body: content }
  )
}

export function redoProjectFile(projectId, fileName, content) {
  return apiFetch(
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}/redo`,
    { method: 'POST', headers: { 'Content-Type': 'text/plain; charset=utf-8' }, body: content }
  )
}

function aiEditProjectFile(projectId, fileName, instruction) {
  return apiFetch(
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${fileName}/ai-edit`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ instruction }) }
  )
}

export function aiEditIndexYml(projectId, instruction) {
  return aiEditProjectFile(projectId, 'index.yml', instruction)
}

export function aiEditIndexCss(projectId, instruction) {
  return aiEditProjectFile(projectId, 'index.css', instruction)
}

export function deleteProjectFile(projectId, fileName) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(fileName)}`, {
    method: 'DELETE'
  })
}

export function postAddLegalTerms(projectId) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/legal-terms`, { method: 'POST' })
}

export function postAddState(projectId) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/states`, { method: 'POST' })
}

export function postAddSignal(projectId) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/signals`, { method: 'POST' })
}

export function postAddEnvKey(projectId) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/env-keys`, { method: 'POST' })
}

export function postAddSource(projectId) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sources`, { method: 'POST' })
}

export function postAddSourceFromFile(projectId, fileName, content) {
  const query = `?file_name=${encodeURIComponent(fileName)}`
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sources${query}`,
    { method: 'POST', headers: { 'Content-Type': 'text/plain; charset=utf-8' }, body: content }
  )
}

export function postAddAction(projectId, stateName) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/actions`,
    { method: 'POST' }
  )
}

export function putStateField(projectId, stateName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putActionField(projectId, stateName, actionName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putInitActionField(projectId, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/init-action/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putSignalField(projectId, signalName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/signals/${encodeURIComponent(signalName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putEnvKeyField(projectId, envKeyName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/env-keys/${encodeURIComponent(envKeyName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putSourceField(projectId, sourceName, field, value) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceName)}/${encodeURIComponent(field)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
  )
}

export function putActionOrder(projectId, stateName, actionName, position) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}/order`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: position }) }
  )
}

export function deleteState(projectId, stateName) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}`, {
    method: 'DELETE'
  })
}

export function deleteProjectAction(projectId, stateName, actionName) {
  return projectFetch(
    projectId,
    `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/states/${encodeURIComponent(stateName)}/actions/${encodeURIComponent(actionName)}`,
    { method: 'DELETE' }
  )
}

export function deleteProjectSignal(projectId, signalName) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/signals/${encodeURIComponent(signalName)}`, {
    method: 'DELETE'
  })
}

export function deleteProjectEnvKey(projectId, envKeyName) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/env-keys/${encodeURIComponent(envKeyName)}`, {
    method: 'DELETE'
  })
}

export function deleteProjectSource(projectId, sourceName) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceName)}`, {
    method: 'DELETE'
  })
}

export function getProjectRevision(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/revision`)
}

export function getPublishPreview(projectId) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/publish/preview`)
}

export function postPublishProject(projectId, remapTo = null) {
  return apiFetch(`${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/publish`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ remap_to: remapTo })
  })
}

export function postRevertProject(projectId) {
  return projectFetch(projectId, `${API_URL}/skills/platform/projects/${encodeURIComponent(projectId)}/revert`, { method: 'POST' })
}

