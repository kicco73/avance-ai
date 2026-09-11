import { ref } from 'vue'
import { getSkills } from './api/skills.js'

const installedKeys = ref(null)

export function isSkillInstalled(key) {
  return installedKeys.value === null || installedKeys.value.includes(key)
}

export async function loadSkillRoster() {
  try {
    const { skills } = await getSkills()
    installedKeys.value = skills.map((skill) => skill.key)
  } catch {
    installedKeys.value = null
  }
}
