import AdminHome from './components/AdminHome.vue'
import ServerOpsActions from './components/ServerOpsActions.vue'
import CustomerHome from './components/appStore/CustomerHome.vue'
import LabelProjectView from './components/project/label/LabelProjectView.vue'
import EditProjectView from './components/project/edit/EditProjectView.vue'
import ManageUsersView from './components/settings/ManageUsersView.vue'
import AppStoreView from './components/appStore/AppStoreView.vue'

export const key = 'platform'

// The screen a role lands on. A build without this directory contributes
// none, and App.vue is left with the one role the core answers by itself:
// a user, in the chat window. That is what a delivered product is.
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

// The deployment operations, offered inside Settings' own Data tab.
export const servicesTabActions = [{ id: 'platform-server-ops', tab: 'database', component: ServerOpsActions }]
