"""The skills collection, as a resource.

skills.py already *is* a collection — it walks backend/src/ at every boot
and that walk is the only list there is. What it lacked was a resource
saying so: the roster was read through GET /api/build/skills, inside a
package a build can leave out, and again through GET /api/services under
a second name for a subset of the same rows. Both are gone; this answers
for all of them, and it is core because the question "what is installed"
is one every build has to be able to answer about itself.

One route, deliberately. There is no GET /api/skills/{key}: nothing asks
a skill about itself that the roster does not already say, and a member
route nobody calls is a promise to keep for free.
"""
from __future__ import annotations

from controllers.base_controller import BaseController, get
from system import skills


class SkillsController(BaseController):

    @get("/api/skills", role="admin")
    def get_skills(self):
        """Every skill this backend has installed: key, package, ui_label,
        ui_description, and whether a project may declare a level for it.
        A caller that wants only the declarable ones filters on that field
        rather than asking a second endpoint for the same rows."""
        return {"skills": skills.installed()}
