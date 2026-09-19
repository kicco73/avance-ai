class EventTriggerNamespace {
  get name() {
    return 'event'
  }

  get color() {
    return '#455a64'
  }

  get proxy() {
    return false
  }

  get emptyLabel() {
    return '(no sibling project shares this family)'
  }

  get emptyHint() {
    return "No other project declares the same project.family — set it in this project's and a sibling's index.yml to reference event.<id>."
  }
}

export const eventTriggerNamespace = new EventTriggerNamespace()
