import { apiFetch } from '../../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'


export function putMessageExpectedState(messageId, expectedState) {
  return apiFetch(`${API_URL}/skills/platform/messages/${encodeURIComponent(messageId)}/expected-state`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_state: expectedState })
  })
}

export function putMessageExpectedSignals(messageId, expectedValues) {
  return apiFetch(`${API_URL}/skills/platform/messages/${encodeURIComponent(messageId)}/expected-signals`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_values: expectedValues })
  })
}

export function putMessageComment(messageId, comment) {
  return apiFetch(`${API_URL}/skills/platform/messages/${encodeURIComponent(messageId)}/comment`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ comment })
  })
}


export function putSessionLabeled(sessionId, labeled) {
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/labeled`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ labeled })
  })
}

export function putSessionTitle(sessionId, title) {
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/title`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title })
  })
}

export function putSessionComment(sessionId, comment) {
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/comment`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ comment })
  })
}

export function deleteSessionAnnotations(sessionId) {
  return apiFetch(`${API_URL}/skills/platform/sessions/${encodeURIComponent(sessionId)}/annotations`, {
    method: 'DELETE'
  })
}
