<script setup>
import { computed } from 'vue'
import { useFloatingTooltip } from '../../useFloatingTooltip.js'

const props = defineProps({
  label: { type: String, required: true },
  value: { type: Number, default: null },
  median: { type: Number, default: null },
  description: { type: String, default: null },
  badgeLabel: { type: String, default: 'Metric' },
  highlighted: { type: Boolean, default: false },
  color: { type: String, default: null }
})

const { visible: tooltipVisible, style: tooltipStyle, show: showTooltip, hide: hideTooltip } = useFloatingTooltip()
const {
  visible: medianTooltipVisible, style: medianTooltipStyle, show: showMedianTooltip, hide: hideMedianTooltip
} = useFloatingTooltip()

const formattedValue = computed(() => (typeof props.value === 'number' ? `${props.value.toFixed(2)}%` : '—'))
const formattedMedian = computed(() => (typeof props.median === 'number' ? `Median: ${props.median.toFixed(2)}%` : ''))
</script>

<template>
  <div class="metric-detail-block">
    <div class="metric-detail-header">
      <span class="metric-detail-badge" :style="color ? { background: color } : null">{{ badgeLabel }}</span>
      <span class="metric-detail-name">{{ label }}</span>
    </div>
    <span v-if="description" class="metric-detail-description">{{ description }}</span>
    <div
      class="metric-detail-bar-track"
      @mouseenter="showTooltip($event.currentTarget)"
      @mouseleave="hideTooltip"
    >
      <div
        v-if="typeof value === 'number'"
        class="metric-detail-bar-fill"
        :class="{ 'metric-detail-bar-changed': highlighted }"
        :style="{ width: value + '%' }"
      ></div>
      <div v-else class="metric-detail-bar-fill metric-detail-bar-na"></div>
      <div
        v-if="typeof median === 'number'"
        class="metric-detail-median-marker"
        :style="{ left: median + '%' }"
        @mouseenter.stop="showMedianTooltip($event.currentTarget)"
        @mouseleave.stop="hideMedianTooltip"
      ></div>
    </div>
    <Teleport to="body">
      <span v-if="tooltipVisible" class="metric-detail-tooltip-floating" :style="tooltipStyle">{{ formattedValue }}</span>
      <span v-if="medianTooltipVisible" class="metric-detail-tooltip-floating" :style="medianTooltipStyle">{{ formattedMedian }}</span>
    </Teleport>
  </div>
</template>

<style scoped>
.metric-detail-block {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.6rem 0.75rem;
  border-radius: 8px;
  border: 1px solid #eee;
  background: #fafafa;
}

.metric-detail-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.metric-detail-badge {
  flex-shrink: 0;
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  color: white;
  background: #ad1457;
}

.metric-detail-name {
  font-weight: 600;
  font-size: 0.85rem;
  color: #333;
}

.metric-detail-description {
  font-size: 0.78rem;
  color: #666;
  line-height: 1.4;
}

.metric-detail-bar-track {
  position: relative;
  margin-top: 0.4rem;
  height: 10px;
  border-radius: 999px;
  background: #eee;
  overflow: visible;
  cursor: default;
}

.metric-detail-bar-fill {
  height: 100%;
  background: #4a6fa5;
  border-radius: 999px;
  transition: width 0.3s ease;
}

.metric-detail-bar-na {
  width: 100%;
  background: repeating-linear-gradient(45deg, #ccc, #ccc 6px, #ddd 6px, #ddd 12px);
}

.metric-detail-median-marker {
  position: absolute;
  top: 0;
  width: 10px;
  height: 100%;
  border-radius: 1.5px;
  background: #d32f2f;
  transform: translateX(-50%);
  cursor: default;
}

@keyframes metric-detail-bar-flash {
  0% { box-shadow: 0 0 0 0 rgba(74, 111, 165, 0.7); filter: brightness(1.35); }
  70% { box-shadow: 0 0 0 5px rgba(74, 111, 165, 0); }
  100% { box-shadow: 0 0 0 0 rgba(74, 111, 165, 0); filter: brightness(1); }
}

.metric-detail-bar-changed {
  animation: metric-detail-bar-flash 0.9s ease-out;
}

.metric-detail-tooltip-floating {
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
