import { defineAsyncComponent } from 'vue'
import { liveModelStore } from './aiModelStore.js'

const AdminHome = defineAsyncComponent(() => import('./components/AdminHome.vue'))
const ServerOpsActions = defineAsyncComponent(() => import('./components/ServerOpsActions.vue'))
const CustomerHome = defineAsyncComponent(() => import('./components/appStore/CustomerHome.vue'))
const LabelProjectView = defineAsyncComponent(() => import('./components/project/label/LabelProjectView.vue'))
const EditProjectView = defineAsyncComponent(() => import('./components/project/edit/EditProjectView.vue'))
const ManageUsersView = defineAsyncComponent(() => import('./components/settings/ManageUsersView.vue'))
const AppStoreView = defineAsyncComponent(() => import('./components/appStore/AppStoreView.vue'))
const EmbedTestChat = defineAsyncComponent(() => import('./components/project/edit/run/EmbedTestChat.vue'))
const PreviewChatEmbed = defineAsyncComponent(() => import('./components/PreviewChatEmbed.vue'))

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

export const embedViews = [
  { key: 'test-chat', component: EmbedTestChat },
  { key: 'preview-chat', component: PreviewChatEmbed },
]
