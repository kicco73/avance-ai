import { computed } from 'vue'
import { isSkillInstalled } from '../skillRoster.js'

const manifests = Object.values(import.meta.glob('./*/index.js', { eager: true }))
  .sort((first, second) => first.key.localeCompare(second.key))

function contributionsOf(point) {
  return computed(() => manifests
    .filter((manifest) => isSkillInstalled(manifest.key))
    .flatMap((manifest) => manifest[point] ?? []))
}

export const compiledSkillKeys = manifests.map((manifest) => manifest.key)

export const pushedViews = contributionsOf('pushedViews')
export const projectActions = contributionsOf('projectActions')
export const projectModes = contributionsOf('projectModes')
export const servicesTabs = contributionsOf('servicesTabs')
export const servicesTabActions = contributionsOf('servicesTabActions')
export const chatInputControls = contributionsOf('chatInputControls')
export const profileFields = contributionsOf('profileFields')
export const messageListeners = contributionsOf('messageListeners')
export const stateListeners = contributionsOf('stateListeners')
export const shareChannels = contributionsOf('shareChannels')
export const liveChatChannels = contributionsOf('liveChatChannels')
export const roleHomes = contributionsOf('roleHomes')

export const channelLabels = computed(() => manifests
  .filter((manifest) => isSkillInstalled(manifest.key))
  .reduce((labels, manifest) => ({ ...labels, ...manifest.channelLabels }), {}))
