<script setup>
import '../../styles/projectCard.css'
import avanceLogoUrl from '../../assets/avance-logo.png'

defineProps({
  row: { type: Object, required: true },
  title: { type: String, required: true },
  description: { type: String, default: null },
  iconSrc: { type: String, default: null }
})

const emit = defineEmits(['icon-error'])
</script>

<template>
  <div class="project-card">
    <span class="project-card-icon-btn">
      <img v-if="iconSrc" :src="iconSrc" class="project-card-icon" alt="" @error="emit('icon-error')" />
      <img v-else :src="avanceLogoUrl" class="project-card-fallback-logo" alt="" />
    </span>
    <div class="project-card-body">
      <span class="project-card-title-row">
        <span class="project-card-title">{{ title }}</span>
        <span v-if="row.broken?.published" class="project-card-broken" :title="row.broken.published">broken</span>
        <span v-if="row.broken?.draft" class="project-card-draft-broken" :title="row.broken.draft">draft broken</span>
        <span
          v-if="row.build_warnings?.length"
          class="project-card-build-warnings"
          :title="row.build_warnings.join('\n')"
        >{{ row.build_warnings.length }} warning{{ row.build_warnings.length === 1 ? '' : 's' }}</span>
      </span>
      <span v-if="description" class="project-card-desc">{{ description }}</span>
    </div>
  </div>
</template>

<style scoped>
.project-card-icon-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border-radius: 15px;
}

.project-card-broken,
.project-card-draft-broken,
.project-card-build-warnings {
  flex-shrink: 0;
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
  font-size: 0.65rem;
  font-weight: 600;
  cursor: help;
}

.project-card-broken {
  background: #fdecea;
  color: #c0392b;
}

.project-card-draft-broken,
.project-card-build-warnings {
  background: #fdf1e3;
  color: #b06a00;
}

.project-card-desc {
  font-size: 0.75rem;
  color: #777;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
