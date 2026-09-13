## Replaying a project

A recorded session replayed against the current project runs the
automaton for real — signals, triggers, transitions and `on-exit` writes
all happen — but **no `task.*` call ever runs during a replay**. A
measurement that sent an email, charged something or wrote to the world
outside would not be a measurement.

That is not the draft-conversation rule (§5.4), which suppresses a real
side effect unless actuators are explicitly enabled and reports it back
as a toast. A replay has nobody to report to: the call is simply not
made, whatever the actuator setting says.
