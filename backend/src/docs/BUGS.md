# Open bugs

Plain record of reported, unresolved bugs: what is observed, where, and
what was already tried. No diagnosis, no cause — that goes in the fix's
own commit once found.

## Eleccion sometimes gets its buttons

Project `entrena_t2`, state `state-0` ("Eleccion", `chat-enabled: false`).
The state offers a patient-case choice; sometimes the frontend does not
receives the buttons for it — the screen shows none.

A previous session believed this was fixed. It was not: the effect still occurs.

If any silent error is found surface it to the UI by channeling an event to be shown if that happens.

Suspects: 
- output a variable by error, caso, in state eleccion, might have caused an error that is never surfaced. try with that on and off, check multiple times, see if flakiness depends on input/output tokens for example. 
- session closed before time by the server for some internal flaky error.
