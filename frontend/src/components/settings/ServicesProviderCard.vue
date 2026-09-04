<script setup>
// Detail card for one ai/talk/listen provider entry (config.yml's own
// providers[]) — same badge/title/open-closed convention as
// InspectorDetailCard.vue's state/action cards and
// InspectorProjectCard.vue's project card, but read-only (Manage
// services never edits config.yml, unlike those) so there's no edit
// form: closed shows the essential fields, open adds the description.
import { computed, ref } from 'vue'
import { renderMarkdown } from '../../markdown.js'
import { useTokensBar } from '../../composables/useTokensBar.js'
import { useFloatingTooltip } from '../../useFloatingTooltip.js'

const props = defineProps({
  provider: { type: Object, required: true }, // {driver, model, url?, ui-label, ui-description, modes?, language?, token-budget-per-day?}
  // Today's own token spend for this provider (see db/ai_usage.py) —
  // null while Manage services > AI hasn't loaded it yet, or for a
  // talk/listen provider (only ai-service ones carry a daily budget).
  usageToday: { type: Number, default: null }
})

const open = ref(false)

const dailyBudget = computed(() => props.provider['token-budget-per-day'])
const { width: tokensBarWidth, level: tokensBarLevel } = useTokensBar(
  computed(() => props.usageToday), dailyBudget
)
const {
  visible: tokensTooltipVisible, style: tokensTooltipStyle, show: showTokensTooltip, hide: hideTokensTooltip
} = useFloatingTooltip()

function toggle() {
  open.value = !open.value
}

function fieldLabel(key) {
  return key.replace(/-/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

// modes/language are pulled out as their own flag badges below, and
// ui-label/ui-description are the title/description — everything else
// (driver, model, url, ...) is shown as a field row.
const fields = computed(() => {
  return Object.entries(props.provider)
    .filter(([key, value]) => !['ui-label', 'ui-description', 'modes', 'language', 'token-budget-per-day'].includes(key) && value != null && value !== '')
    .map(([key, value]) => [fieldLabel(key), String(value)])
})

// "no-auto" gets its own highlighted variant — it's the one badge that
// actually changes this provider's behavior (opts it out of auto-live/
// auto-test selection), so it shouldn't blend in with the purely
// descriptive live/test/language ones.
const flagBadges = computed(() => {
  const badges = []
  if (Array.isArray(props.provider.modes)) {
    for (const mode of props.provider.modes) {
      badges.push({ key: mode, label: fieldLabel(mode), highlight: mode === 'no-auto' })
    }
  }
  if (props.provider.language) badges.push({ key: 'language', label: props.provider.language.toUpperCase(), highlight: false })
  return badges
})
</script>

<template>
  <div class="inspector-detail-card" :class="{ 'inspector-detail-card-open': open }" @click="toggle">
    <div class="inspector-detail-header">
      <div class="inspector-detail-header-top">
        <span class="inspector-detail-badge inspector-detail-badge-provider">Provider</span>
        <span class="inspector-detail-title">{{ provider['ui-label'] || provider.driver }}</span>
      </div>
      <div v-if="flagBadges.length" class="inspector-detail-badges">
        <span
          v-for="badge in flagBadges"
          :key="badge.key"
          class="inspector-detail-badge"
          :class="badge.highlight ? 'inspector-detail-badge-no-auto' : 'inspector-detail-badge-flag'"
          :title="badge.highlight ? 'Excluded from auto-live/auto-test selection — only reachable by picking it manually' : null"
        >{{ badge.label }}</span>
      </div>
      <div v-if="dailyBudget != null && usageToday != null" class="services-provider-tokens">
        <span class="services-provider-tokens-label">Today</span>
        <div
          class="services-provider-tokens-bar-track"
          @click.stop
          @mouseenter="showTokensTooltip($event.currentTarget)"
          @mouseleave="hideTokensTooltip"
        >
          <div
            class="services-provider-tokens-bar-fill"
            :class="`services-provider-tokens-bar-fill-${tokensBarLevel}`"
            :style="{ width: tokensBarWidth }"
          ></div>
        </div>
      </div>
    </div>
    <Teleport to="body">
      <span
        v-if="tokensTooltipVisible"
        class="services-provider-tokens-tooltip-floating"
        :style="tokensTooltipStyle"
      >{{ usageToday.toLocaleString() }} / {{ dailyBudget.toLocaleString() }} tokens today</span>
    </Teleport>
    <div class="inspector-detail-body">
      <Transition name="crossfade" mode="out-in">
        <div v-if="open" key="open">
          <div v-for="[label, value] in fields" :key="label" class="services-field">
            <label class="services-field-label">{{ label }}</label>
            <input class="services-field-input" type="text" :value="value" disabled />
          </div>
          <div
            v-if="provider['ui-description']"
            class="inspector-detail-ui_description"
            v-html="renderMarkdown(provider['ui-description'])"
          ></div>
        </div>
        <div v-else key="closed">
          <p v-for="[label, value] in fields" :key="label" class="services-provider-field">
            <strong>{{ label }}:</strong> {{ value }}
          </p>
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.inspector-detail-card { cursor: pointer; display: flex; flex-direction: column; border-radius: 8px; border: 1px solid #eee; background: #fafafa; overflow: hidden; margin: 0.75rem 0; }
.inspector-detail-header { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.5rem 0.6rem; border-bottom: 1px solid #eee; flex-shrink: 0; }
.inspector-detail-header-top { display: flex; align-items: center; gap: 0.5rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-provider { background: #4a6fa5; }
.inspector-detail-badges { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.inspector-detail-badge-flag { background: #eee; color: #555; }
.inspector-detail-badge-no-auto { background: #f5a623; color: #3a2600; }
.inspector-detail-title { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.inspector-detail-body { padding: 0.6rem 0.75rem; font-size: 0.8rem; color: #444; }
.inspector-detail-ui_description { margin: 0.5rem 0 0; line-height: 1.4; }
.crossfade-enter-active, .crossfade-leave-active { transition: opacity 0.15s ease; }
.crossfade-enter-from, .crossfade-leave-to { opacity: 0; }

/* Closed state: plain "label: value" text — same idiom as
   InspectorDetailCard.vue's own .inspector-detail-field, not the styled
   inputs below (those are for the open state only). */
.services-provider-field { margin: 0 0 0.4rem; line-height: 1.4; }
.services-provider-field:last-child { margin-bottom: 0; }
.services-provider-field strong { color: #555; margin-right: 0.3rem; }

/* Open state: the same field-as-disabled-input look every other Manage
   services tab uses (see ServicesView.vue's own identically-named rules
   — duplicated here since scoped styles don't cross component
   boundaries). */
.services-field { display: flex; flex-direction: column; gap: 0.25rem; margin: 0 0 0.75rem; max-width: 420px; }
.services-field:last-child { margin-bottom: 0; }
.services-field-label { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; color: #777; }
.services-field-input { width: 100%; box-sizing: border-box; padding: 0.4rem 0.6rem; border: 1px solid #ddd; border-radius: 6px; background: #f5f5f7; color: #333; font: inherit; font-size: 0.85rem; }
.services-field-input:disabled { opacity: 1; cursor: default; -webkit-text-fill-color: #333; }

/* Daily consumption bar — same green/orange/red idiom as
   SessionDetailCard.vue's own .session-detail-tokens (duplicated here
   for the same scoped-styles-don't-cross-boundaries reason as
   .services-field above), shown regardless of open/closed. */
.services-provider-tokens { display: flex; align-items: center; gap: 0.4rem; }
.services-provider-tokens-label { flex-shrink: 0; font-size: 0.68rem; color: #888; }
.services-provider-tokens-bar-track { position: relative; flex: 1; min-width: 40px; height: 6px; border-radius: 999px; background: #eee; overflow: hidden; cursor: default; }
.services-provider-tokens-bar-fill { height: 100%; border-radius: 999px; transition: width 0.3s ease; }
.services-provider-tokens-bar-fill-green { background: #2e7d32; }
.services-provider-tokens-bar-fill-orange { background: #f5a623; }
.services-provider-tokens-bar-fill-red { background: #c62828; }
</style>

<style>
/* FIXME: unscoped on purpose — teleported to <body>, outside scoped CSS reach. */
.services-provider-tokens-tooltip-floating {
  position: fixed;
  width: max-content;
  max-width: 200px;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
  background: #333;
  color: white;
  font-size: 0.72rem;
  font-weight: 400;
  line-height: 1.3;
  text-align: left;
  pointer-events: none;
  z-index: 1000;
}
</style>
