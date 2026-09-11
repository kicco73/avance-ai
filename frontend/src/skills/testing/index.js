import TestModePanel from './components/TestModePanel.vue'
import TestInfoTab from './components/TestInfoTab.vue'
import TestUserTab from './components/TestUserTab.vue'
import ServicesTestingTab from './components/ServicesTestingTab.vue'

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
