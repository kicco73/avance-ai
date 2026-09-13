const observers = []

export function observeMessages(newObservers) {
  observers.splice(0, observers.length, ...newObservers)
}

export function messageArrived(messageId) {
  for (const observer of observers) observer.messageArrived(messageId)
}
