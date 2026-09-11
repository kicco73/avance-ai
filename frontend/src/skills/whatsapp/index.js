import ProfilePhoneField from './components/ProfilePhoneField.vue'
import ServicesTab from './components/ServicesTab.vue'

export const key = 'whatsapp'

export const servicesTabs = [{ id: 'whatsapp', label: 'WhatsApp', component: ServicesTab }]

export const profileFields = [{ id: 'whatsapp-phone', component: ProfilePhoneField }]

export const shareChannels = [{
  id: 'whatsapp',
  label: 'WhatsApp',
  inviteField: 'whatsapp_url',
  hint: 'Scan to open WhatsApp with this invite ready to send.'
}]

export const channelLabels = { 'whatsapp-chat': 'WhatsApp' }
