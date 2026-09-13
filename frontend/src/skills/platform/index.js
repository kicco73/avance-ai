import AdminHome from './components/AdminHome.vue'
import ServerOpsActions from './components/ServerOpsActions.vue'
import { liveModelStore } from './aiModelStore.js'
import CustomerHome from './components/appStore/CustomerHome.vue'
import LabelProjectView from './components/project/label/LabelProjectView.vue'
import EditProjectView from './components/project/edit/EditProjectView.vue'
import ManageUsersView from './components/settings/ManageUsersView.vue'
import AppStoreView from './components/appStore/AppStoreView.vue'

export const key = 'platform'

export const roleHomes = [
  { role: 'admin', component: AdminHome },
  { role: 'supervisor', component: LabelProjectView },
  { role: 'customer', component: CustomerHome },
]

export const pushedViews = [
  { view: 'edit', component: EditProjectView },
  { view: 'label', component: LabelProjectView },
  { view: 'manageUsers', component: ManageUsersView },
  { view: 'appStore', component: AppStoreView },
]

export const servicesTabActions = [{ id: 'platform-server-ops', tab: 'database', component: ServerOpsActions }]

export const modelSelectors = [liveModelStore]
