<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getSessionRating, postSessionRating } from '../../api/chat.js'

const props = defineProps({
  sessionId: { type: [Number, String], required: true }
})

const emit = defineEmits(['update:active'])

const loaded = ref(false)
const alreadyRated = ref(false)
const thanked = ref(false)
const submitting = ref(false)

const active = computed(() => loaded.value && !alreadyRated.value)

watch(active, (value) => emit('update:active', value), { immediate: true })

async function load(sessionId) {
  loaded.value = false
  thanked.value = false
  try {
    const { rating } = await getSessionRating(sessionId)
    alreadyRated.value = rating != null
  } catch {
    alreadyRated.value = false
  } finally {
    loaded.value = true
  }
}

watch(() => props.sessionId, (sessionId) => {
  if (sessionId != null) load(sessionId)
}, { immediate: false })

onMounted(() => load(props.sessionId))

async function vote(rating) {
  if (submitting.value) return
  submitting.value = true
  try {
    await postSessionRating(props.sessionId, rating)
    thanked.value = true
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div v-if="active" class="session-rating">
    <Transition name="session-rating-fade" mode="out-in">
      <div v-if="!thanked" key="ask" class="session-rating-ask">
        <span class="session-rating-question">How was your experience?</span>
        <button
          type="button"
          class="session-rating-btn"
          :disabled="submitting"
          aria-label="Thumb up"
          @click="vote(5)"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <path d="M7 11v9H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h3Zm0 0 5.5-7.5a1.5 1.5 0 0 1 2.7 1l-1 5.5h5a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 18 21H9.5a2.5 2.5 0 0 1-2.5-2.5" />
          </svg>
        </button>
        <button
          type="button"
          class="session-rating-btn"
          :disabled="submitting"
          aria-label="Thumb down"
          @click="vote(1)"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <path d="M17 13V4h3a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-3Zm0 0-5.5 7.5a1.5 1.5 0 0 1-2.7-1l1-5.5h-5a2 2 0 0 1-2-2.3l1.2-7A2 2 0 0 1 6 3h8.5A2.5 2.5 0 0 1 17 5.5" />
          </svg>
        </button>
      </div>
      <div v-else key="thanks" class="session-rating-thanks">Thank you!</div>
    </Transition>
  </div>
</template>

<style scoped>
.session-rating {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0.6rem 1rem;
  background: #f5f5f7;
  border-top: 1px solid #e5e5ea;
  min-height: 2.5rem;
}

.session-rating-ask {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0.6rem;
  width: 100%;
  font-size: 0.85rem;
  color: #444;
}

.session-rating-question {
  margin-right: auto;
  padding-left: 10px;
  font-size: 20px;
  font-family: sans-serif;
  color: gray;
}

.session-rating-thanks {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding-left: 10px;
  font-size: 20px;
  font-family: sans-serif;
  color: gray;
}

.session-rating-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.8rem;
  height: 1.8rem;
  border: 1px solid #b8b8bf;
  background: none;
  padding: 0;
  color: #444;
  cursor: pointer;
  border-radius: 6px;
}

.session-rating-btn:hover:not(:disabled) {
  background: #e5e5ea;
  border-color: #8c8c94;
}

.session-rating-btn:disabled {
  cursor: default;
  opacity: 0.5;
}

.session-rating-thanks {
  font-weight: 500;
}

.session-rating-fade-enter-active,
.session-rating-fade-leave-active {
  transition: opacity 0.25s ease;
}

.session-rating-fade-enter-from,
.session-rating-fade-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .session-rating-fade-enter-active,
  .session-rating-fade-leave-active {
    transition: none;
  }
}
</style>
