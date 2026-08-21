<script setup>
import { onMounted, ref } from 'vue'
import { getUsers } from '../../api.js'
import ErrorBanner from '../ErrorBanner.vue'

const emit = defineEmits(['close'])

const rows = ref([])
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const res = await getUsers()
    rows.value = res.users
  } catch {
  } finally {
    loading.value = false
  }
}

function formatDate(iso) {
  return iso ? new Date(iso).toLocaleString() : '—'
}

onMounted(load)

defineExpose({ refresh: load })
</script>

<template>
  <div class="manage-users-overlay">
    <div class="manage-users-header">
      <h2>Users</h2>
      <div class="manage-users-header-actions">
        <button class="close-btn" @click="emit('close')">Back</button>
      </div>
    </div>

    <ErrorBanner />

    <div class="manage-users-body">
      <p v-if="loading" class="manage-users-status">Loading…</p>
      <p v-else-if="!rows.length" class="manage-users-status">No users yet.</p>

      <table v-else class="manage-users-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Email</th>
            <th>Provider</th>
            <th>Created</th>
            <th>Last login</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id">
            <td class="manage-users-name">{{ row.name ?? '—' }}</td>
            <td>{{ row.email }}</td>
            <td>{{ row.provider ?? '—' }}</td>
            <td>{{ formatDate(row.created_at) }}</td>
            <td>{{ formatDate(row.last_login) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.manage-users-overlay {
  position: fixed;
  inset: 0;
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.manage-users-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #ddd;
}

.manage-users-header h2 {
  margin: 0;
  font-size: 1.1rem;
}

.manage-users-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.close-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.close-btn:hover {
  background: #4a6fa5;
  color: white;
}

.manage-users-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 1rem;
}

.manage-users-status {
  margin: 0;
  padding: 0.75rem 0;
  font-size: 0.9rem;
  color: #666;
}

.manage-users-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
}

.manage-users-table th {
  text-align: left;
  padding: 0.5rem 0.75rem;
  border-bottom: 2px solid #ddd;
  color: #555;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.manage-users-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #eee;
  vertical-align: middle;
}

.manage-users-name {
  font-weight: 600;
  color: #333;
}
</style>
