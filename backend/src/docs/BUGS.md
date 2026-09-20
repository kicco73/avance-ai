# Open bugs

Plain record of reported, unresolved bugs: what is observed, where, and
what was already tried. No diagnosis, no cause — that goes in the fix's
own commit once found.

## The last action button stays painted in the next state

Reported on iOS. After tapping an action button, the button in that
position comes back painted as if it were still selected — white
background, the hover fill — when the next state's row is drawn. Tapping
anywhere else in the page clears it.

Where: `frontend/src/components/chat/ActionButtons.vue`, the skin's
`.action-btn:hover:not(:disabled)` rule.

Tried, and not enough: keying every button on a presentation counter so
the row's DOM nodes are replaced whenever the actions change or the row
is re-enabled (commit b03a627f). The painted state survives it.

## The assistant bubble comes back whitened

Reported on iOS. An assistant bubble loses its own background and is
drawn white after it finishes showing (correcly) the text streaming coming from the bus. this happens on ios and only if the scroll view is bigger than the view hosting it (vertically scrolled downwards). 


Where: `frontend/src/components/chat/MessageBubble.vue`, under a project
skin.

Not yet reproduced from a written-down sequence: the conditions that
bring it on — which skin, whether it is the streaming bubble, whether a
scroll or a state change precedes it — are still unrecorded.
