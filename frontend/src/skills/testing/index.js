import { defineAsyncComponent } from 'vue'

const TestModePanel = defineAsyncComponent(() => import('./components/TestModePanel.vue'))
const TestInfoTab = defineAsyncComponent(() => import('./components/TestInfoTab.vue'))
const TestUserTab = defineAsyncComponent(() => import('./components/TestUserTab.vue'))
const ServicesTestingTab = defineAsyncComponent(() => import('./components/ServicesTestingTab.vue'))

export const key = 'testing'

export const projectModes = [{
  id: 'test',
  label: 'Test',
  panel: TestModePanel,
  inspectorTabs: [
    { id: 'testing-info', label: 'Info', component: TestInfoTab },
    { id: 'testing-user', label: 'User', component: TestUserTab }
  ]
}]

export const servicesTabs = [{ id: 'testing', label: 'Testing', component: ServicesTestingTab }]
