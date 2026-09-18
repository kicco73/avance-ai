import { defineAsyncComponent } from 'vue'
import ProjectBuildButton from './components/ProjectBuildButton.vue'

const BuildProjectView = defineAsyncComponent(() => import('./components/BuildProjectView.vue'))
const ServicesBuildTab = defineAsyncComponent(() => import('./components/ServicesBuildTab.vue'))

export const key = 'build'

export const pushedViews = [{ view: 'build', component: BuildProjectView }]

export const projectActions = [{ id: 'build', component: ProjectBuildButton, opens: 'build' }]

export const servicesTabs = [{ id: 'build', label: 'Build', component: ServicesBuildTab }]
