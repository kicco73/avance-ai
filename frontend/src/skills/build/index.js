import BuildProjectView from './components/BuildProjectView.vue'
import ProjectBuildButton from './components/ProjectBuildButton.vue'
import ServicesBuildTab from './components/ServicesBuildTab.vue'

export const key = 'build'

export const pushedViews = [{ view: 'build', component: BuildProjectView }]

export const projectActions = [{ id: 'build', component: ProjectBuildButton, opens: 'build' }]

export const servicesTabs = [{ id: 'build', label: 'Build', component: ServicesBuildTab }]
