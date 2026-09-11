// Who gets told that a reply has arrived. The chat store announces here
// rather than importing the registry: this module is a leaf, so a skill
// is free to reach back into the store it is contributing to.
const observers = []

export function observeMessages(newObservers) {
  observers.splice(0, observers.length, ...newObservers)
}

export function messageArrived(messageId) {
  for (const observer of observers) observer.messageArrived(messageId)
}
