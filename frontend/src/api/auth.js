import { apiFetch } from './core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function getState(signal) {
  return apiFetch(`${API_URL}/core/state`, { signal })
}

export function postLogin(provider, credential) {
  return apiFetch(`${API_URL}/core/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, credential })
  })
}

export function postLogout() {
  return apiFetch(`${API_URL}/core/auth/logout`, { method: 'POST' })
}

export function getTerms() {
  return apiFetch(`${API_URL}/core/auth/terms`)
}

export function postAcceptTerms(inviteCode) {
  return apiFetch(`${API_URL}/core/auth/terms/acceptance`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ invite_code: inviteCode ?? null })
  })
}

export function getLegalTermsStatus(projectId) {
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/legal-terms/status`)
}

export function postAcceptProjectTerms(projectId) {
  return apiFetch(`${API_URL}/core/projects/${encodeURIComponent(projectId)}/legal-terms/acceptance`, { method: 'POST' })
}

export function postEraseData() {
  return apiFetch(`${API_URL}/core/auth/erase-data`, { method: 'POST' })
}

export function getMe() {
  return apiFetch(`${API_URL}/core/auth/me`)
}

export function putUserPhoneNumber(phoneNumber, confirmMerge = false) {
  return apiFetch(`${API_URL}/core/auth/me/phone-number`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ phone_number: phoneNumber, confirm_merge: confirmMerge })
  })
}

export function getPendingStatus() {
  return apiFetch(`${API_URL}/core/auth/pending-status`)
}

export function getAuthProviders() {
  return apiFetch(`${API_URL}/core/auth/providers`)
}
