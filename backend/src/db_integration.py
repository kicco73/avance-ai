"""Small integration helper/documentation for the application's Db facade.

The framework cannot import Peewee by design. Add the following method to the
existing Db class (the module that already owns the Peewee models):

    def get_signals(self, session_id: int) -> list[dict]:
        rows = (
            Signals.select()
            .where(Signals.session == session_id)
            .order_by(Signals.timestamp.asc(), Signals.id.asc())
        )
        return [
            {
                "id": row.id,
                "timestamp": row.timestamp.isoformat(),
                "values": row.values,
                "old_state": row.old_state,
                "action": row.action,
                "new_state": row.new_state,
            }
            for row in rows
        ]

Keeping this method in the application's DB module preserves the project's
single-point-of-database-access rule.
"""
