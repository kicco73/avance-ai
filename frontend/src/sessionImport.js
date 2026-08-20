// Pure, framework-agnostic summary logic for LabelProjectView.vue's
// batch transcript import (see handleImportSession) — extracted the same
// way as benchmarkTimeline.js's own functions, so the "N of M imported,
// here's why the rest failed" message has a real regression test instead
// of only ever having been eyeballed.

// Builds the <ErrorBanner /> message/detail pair for a batch import's
// per-file results (see handleImportSession). One File failing is never
// allowed to abort the rest (see SessionsPanel.vue's own `multiple` file
// input), so unlike every other apiFetch failure — always a single
// message/detail pair, see errorStore.js — a batch can have anywhere from
// none to every file fail alongside others that succeeded. Returns null
// when nothing failed, so the caller knows to clear any stale error left
// over from a *previous* import instead of leaving it showing.
//
// `results` is [{ file, ok, error }], one entry per File in the batch, in
// the same order they were picked — `error` is only meaningful when
// `ok` is false (see api.js's apiFetch, which throws Error(message) on
// any failed import).
export function summarizeImportFailures(results) {
  const failures = results.filter((r) => !r.ok)
  if (!failures.length) return null

  const message = failures.length === results.length
    ? `Failed to import ${failures.length === 1 ? 'the transcript' : `all ${failures.length} transcripts`}.`
    : `Imported ${results.length - failures.length} of ${results.length} transcripts — ${failures.length} failed.`
  const detail = failures.map((r) => `${r.file.name}: ${r.error}`).join('\n')
  return { message, detail }
}
