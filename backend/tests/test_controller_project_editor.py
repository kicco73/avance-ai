"""index.yml structural editing endpoints — add/edit/delete/reorder
states, actions, and signals without hand-writing YAML. Checked here by
reading the persisted YAML back after each write, not by asserting on
the raw text directly.
"""
from __future__ import annotations

import io
import re
import zipfile

import pytest

from conftest import parse_sse_result

pytestmark = pytest.mark.contract


def _index_yml(client, project_name: str) -> str:
    response = client.get(f"/api/projects/{project_name}/files/index.yml")
    assert response.status_code == 200
    return response.json()["content"]


class TestAddState:
    def test_creates_a_new_state_and_persists_it(self, client, hello_project):
        response = client.post(f"/api/projects/{hello_project}/states")
        assert response.status_code == 200
        payload = response.json()

        assert payload["key"] == "state-0"
        assert payload["ui_label"] == "New State"
        assert payload["final"] is True
        assert payload["actions"] == []
        assert "state-0:" in _index_yml(client, hello_project)

    def test_unknown_project_is_404(self, client):
        response = client.post("/api/projects/does-not-exist/states")
        assert response.status_code == 404


class TestAddSignal:
    def test_creates_a_new_signal_and_persists_it(self, client, hello_project):
        response = client.post(f"/api/projects/{hello_project}/signals")
        assert response.status_code == 200
        payload = response.json()

        assert payload["name"] == "new_signal"
        assert payload["ui_label"] == "New Signal"
        assert "new_signal:" in _index_yml(client, hello_project)


class TestAddAction:
    def test_creates_a_new_action_scoped_to_the_given_state(self, client, hello_project):
        response = client.post(f"/api/projects/{hello_project}/states/Hello/actions")
        assert response.status_code == 200
        payload = response.json()

        assert payload["name"] == "action-0"
        assert payload["ui_label"] == "New Action"
        assert payload["target"] == "Hello"  # self-loop
        assert payload["has_trigger"] is False

    def test_unknown_state_is_400(self, client, hello_project):
        response = client.post(f"/api/projects/{hello_project}/states/does-not-exist/actions")
        assert response.status_code == 400


class TestPutStateField:
    def test_edits_ui_label(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/states/Hello/ui-label", json={"value": "Greeting"})
        assert response.status_code == 200
        assert response.json()["ui_label"] == "Greeting"
        assert "Greeting" in _index_yml(client, hello_project)

    def test_edits_history_cutoff_as_a_boolean(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/states/Hello/history-cutoff", json={"value": True})
        assert response.status_code == 200

    def test_edits_ui_description(self, client, hello_project):
        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/ui-description", json={"value": "A friendly greeting."}
        )
        assert response.status_code == 200
        assert response.json()["ui_description"] == "A friendly greeting."
        assert "A friendly greeting." in _index_yml(client, hello_project)

    def test_rejects_a_field_not_on_the_whitelist(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/states/Hello/fixed-message", json={"value": "x"})
        assert response.status_code == 400

    def test_trims_leading_and_trailing_whitespace(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/states/Hello/ui-label", json={"value": "  Greeting  "})
        assert response.status_code == 200
        assert response.json()["ui_label"] == "Greeting"


class TestPutActionField:
    def test_edits_target(self, client, hello_project):
        client.post(f"/api/projects/{hello_project}/states")  # state-0
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()

        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/target",
            json={"value": "state-0"},
        )
        assert response.status_code == 200
        assert response.json()["target"] == "state-0"

    def test_edits_ui_description(self, client, hello_project):
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/ui-description",
            json={"value": "A short action description."},
        )
        assert response.status_code == 200
        assert response.json()["ui_description"] == "A short action description."
        assert "A short action description." in _index_yml(client, hello_project)

    def test_edits_trigger(self, client, hello_project):
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/trigger",
            json={"value": "True"},
        )
        assert response.status_code == 200
        assert response.json()["has_trigger"] is True
        assert "trigger:" in _index_yml(client, hello_project)

    def test_an_invalid_trigger_returns_structured_error_fields(self, client, hello_project):
        """See AutomatonBuildError — CodeEditor.vue's own jump-to-error
        needs project_id/file/line/section, not just a message string."""
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()

        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/trigger",
            json={"value": "signal.definitely_not_declared == 1"},
        )

        assert response.status_code == 400
        fields = response.json()["error"]["fields"]
        assert fields["project_id"] == hello_project
        assert fields["file"] == "index.yml"
        assert fields["section"] == f"states.Hello.actions.{action['name']}"
        assert isinstance(fields["line"], int)
        assert isinstance(fields["revision"], int)

    def test_clearing_trigger_removes_it_rather_than_storing_an_empty_string(self, client, hello_project):
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/trigger",
            json={"value": "signal.mood == 'happy'"},
        )
        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/trigger",
            json={"value": ""},
        )
        assert response.status_code == 200
        assert response.json()["has_trigger"] is False
        assert "trigger" not in _index_yml(client, hello_project)

    def test_edits_on_enter(self, client, hello_project):
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/on-enter",
            json={"value": "actuator.notify('Nice!', 'You reached **state B**.')"},
        )
        assert response.status_code == 200
        assert response.json()["on-enter"] == "actuator.notify('Nice!', 'You reached **state B**.')"
        assert "on-enter:" in _index_yml(client, hello_project)

    def test_rejects_a_field_not_on_the_whitelist(self, client, hello_project):
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/not-a-real-field",
            json={"value": "anything"},
        )
        assert response.status_code == 400

    def test_edits_env(self, client, hello_project):
        env_key = client.post(f"/api/projects/{hello_project}/env-keys").json()
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/env",
            json={"value": {env_key["name"]: "1"}},
        )
        assert response.status_code == 200
        assert f"{env_key['name']}:" in _index_yml(client, hello_project)

    def test_clearing_env_removes_it_rather_than_storing_an_empty_mapping(self, client, hello_project):
        env_key = client.post(f"/api/projects/{hello_project}/env-keys").json()
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/env",
            json={"value": {env_key["name"]: "1"}},
        )
        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/env",
            json={"value": {}},
        )
        assert response.status_code == 200
        # Exactly one "env:" left — the project-level declaration itself;
        # the action's own (now emptied) one was removed, not left as `env: {}`.
        assert _index_yml(client, hello_project).count("env:") == 1

    def test_env_writing_to_an_undeclared_key_is_400(self, client, hello_project):
        """The usual parsing/validation pass (AutomatonBuilder, run via
        prepare_update on every save) still gates this field — an action
        can only set an env key that's already declared project-wide."""
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/env",
            json={"value": {"never_declared_anywhere": "1"}},
        )
        assert response.status_code == 400


class TestPutSignalField:
    def test_edits_definition(self, client, hello_project):
        signal = client.post(f"/api/projects/{hello_project}/signals").json()
        response = client.put(
            f"/api/projects/{hello_project}/signals/{signal['name']}/definition",
            json={"value": "a real definition"},
        )
        assert response.status_code == 200
        assert response.json()["definition"] == "a real definition"

    def test_edits_ui_description(self, client, hello_project):
        signal = client.post(f"/api/projects/{hello_project}/signals").json()
        response = client.put(
            f"/api/projects/{hello_project}/signals/{signal['name']}/ui-description",
            json={"value": "A short human description."},
        )
        assert response.status_code == 200
        assert response.json()["ui_description"] == "A short human description."

    def test_ui_label_edit_renames_the_signal(self, client, hello_project):
        signal = client.post(f"/api/projects/{hello_project}/signals").json()  # new_signal
        response = client.put(
            f"/api/projects/{hello_project}/signals/{signal['name']}/ui-label",
            json={"value": "Risk Level"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["name"] == "risk_level"
        assert payload["ui_label"] == "Risk Level"
        assert "risk_level:" in _index_yml(client, hello_project)

    def test_rejects_a_field_not_on_the_whitelist(self, client, hello_project):
        signal = client.post(f"/api/projects/{hello_project}/signals").json()
        response = client.put(
            f"/api/projects/{hello_project}/signals/{signal['name']}/attachments",
            json={"value": "x"},
        )
        assert response.status_code == 400


class TestPutInitActionTarget:
    def test_moves_the_start_state(self, client, hello_project):
        client.post(f"/api/projects/{hello_project}/states")  # state-0
        response = client.put(
            f"/api/projects/{hello_project}/init-action/target", json={"value": "state-0"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["key"] == "state-0"

        # Now that state-0 is the start state, Hello can be deleted and
        # state-0 can't.
        assert client.delete(f"/api/projects/{hello_project}/states/Hello").status_code == 204
        assert client.delete(f"/api/projects/{hello_project}/states/state-0").status_code == 400

    def test_rejects_an_unknown_state(self, client, hello_project):
        response = client.put(
            f"/api/projects/{hello_project}/init-action/target", json={"value": "does-not-exist"}
        )
        assert response.status_code == 400
        assert "Hello:" in _index_yml(client, hello_project)


class TestPutInitActionField:
    """The init-action is an action like any other action-field edit —
    same payload shape, same endpoint pattern, same env editing — minus
    'trigger', which AutomatonBuilder's own _build_init_action never
    reads for it (the init-action is the automaton's unconditional
    entry point, never conditionally fired)."""

    def test_edits_ui_description(self, client, hello_project):
        response = client.put(
            f"/api/projects/{hello_project}/init-action/ui-description",
            json={"value": "Where every session begins."},
        )
        assert response.status_code == 200
        assert response.json()["ui_description"] == "Where every session begins."
        assert "Where every session begins." in _index_yml(client, hello_project)

    def test_edits_on_enter(self, client, hello_project):
        response = client.put(
            f"/api/projects/{hello_project}/init-action/on-enter",
            json={"value": "actuator.celebrate()"},
        )
        assert response.status_code == 200
        assert response.json()["on-enter"] == "actuator.celebrate()"

    def test_has_trigger_is_always_false(self, client, hello_project):
        response = client.put(
            f"/api/projects/{hello_project}/init-action/ui-label",
            json={"value": "Start"},
        )
        assert response.status_code == 200
        assert response.json()["has_trigger"] is False

    def test_rejects_trigger(self, client, hello_project):
        response = client.put(
            f"/api/projects/{hello_project}/init-action/trigger",
            json={"value": "True"},
        )
        assert response.status_code == 400

    def test_edits_env(self, client, hello_project):
        env_key = client.post(f"/api/projects/{hello_project}/env-keys").json()
        response = client.put(
            f"/api/projects/{hello_project}/init-action/env",
            json={"value": {env_key["name"]: "1"}},
        )
        assert response.status_code == 200
        assert f"{env_key['name']}:" in _index_yml(client, hello_project)

    def test_clearing_env_removes_it_rather_than_storing_an_empty_mapping(self, client, hello_project):
        env_key = client.post(f"/api/projects/{hello_project}/env-keys").json()
        client.put(
            f"/api/projects/{hello_project}/init-action/env",
            json={"value": {env_key["name"]: "1"}},
        )
        response = client.put(
            f"/api/projects/{hello_project}/init-action/env",
            json={"value": {}},
        )
        assert response.status_code == 200
        assert "env: {}" not in _index_yml(client, hello_project)

    def test_rejects_an_env_key_that_was_never_declared(self, client, hello_project):
        response = client.put(
            f"/api/projects/{hello_project}/init-action/env",
            json={"value": {"never_declared": "1"}},
        )
        assert response.status_code == 400

    def test_rejects_a_field_not_on_the_whitelist(self, client, hello_project):
        response = client.put(
            f"/api/projects/{hello_project}/init-action/not-a-real-field",
            json={"value": "anything"},
        )
        assert response.status_code == 400


class TestReorderActions:
    def test_moves_an_action_and_returns_the_new_order(self, client, hello_project):
        first = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        second = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()

        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{second['name']}/order",
            json={"value": 0},
        )
        assert response.status_code == 200
        assert [a["name"] for a in response.json()] == [second["name"], first["name"]]

    def test_out_of_range_position_is_400(self, client, hello_project):
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/order",
            json={"value": 99},
        )
        assert response.status_code == 400


class TestDeleteState:
    def test_removes_it(self, client, hello_project):
        client.post(f"/api/projects/{hello_project}/states")  # state-0
        response = client.delete(f"/api/projects/{hello_project}/states/state-0")
        assert response.status_code == 204
        assert "state-0:" not in _index_yml(client, hello_project)

    def test_refuses_to_delete_the_init_actions_own_target(self, client, hello_project):
        response = client.delete(f"/api/projects/{hello_project}/states/Hello")
        assert response.status_code == 400
        assert "Hello:" in _index_yml(client, hello_project)


class TestDeleteAction:
    def test_removes_it_without_touching_the_state(self, client, hello_project):
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        response = client.delete(f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}")
        assert response.status_code == 204
        assert f"{action['name']}" not in _index_yml(client, hello_project)
        assert "Hello:" in _index_yml(client, hello_project)


class TestDeleteSignal:
    def test_removes_it(self, client, hello_project):
        signal = client.post(f"/api/projects/{hello_project}/signals").json()
        response = client.delete(f"/api/projects/{hello_project}/signals/{signal['name']}")
        assert response.status_code == 204
        assert f"{signal['name']}:" not in _index_yml(client, hello_project)


def _archive_content(client, project_name: str, archive_name: str):
    response = client.get(f"/api/projects/{project_name}/files/{archive_name}")
    assert response.status_code == 200
    return response.json()


class TestAddSource:
    def test_creates_a_source_with_its_own_empty_archive(self, client, hello_project):
        response = client.post(f"/api/projects/{hello_project}/sources")
        assert response.status_code == 200
        payload = response.json()

        assert payload["name"] == "behaviour"
        assert payload["url"] == "avance:sources/behaviour.csv"
        assert "behaviour:" in _index_yml(client, hello_project)
        assert _archive_content(client, hello_project, "sources/behaviour.csv")["content"] == ""

    def test_name_collisions_get_suffixed_and_get_their_own_archive_too(self, client, hello_project):
        client.post(f"/api/projects/{hello_project}/sources")  # behaviour
        second = client.post(f"/api/projects/{hello_project}/sources").json()

        assert second["name"] == "behaviour1"
        assert second["url"] == "avance:sources/behaviour1.csv"
        assert _archive_content(client, hello_project, "sources/behaviour1.csv")["content"] == ""


class TestPutSourceField:
    def test_ui_label_and_ui_description_are_plain_edits(self, client, hello_project):
        source = client.post(f"/api/projects/{hello_project}/sources").json()

        response = client.put(
            f"/api/projects/{hello_project}/sources/{source['name']}/ui-label", json={"value": "Flights"}
        )
        assert response.status_code == 200
        assert response.json()["ui_label"] == "Flights"

    def test_url_is_not_an_editable_field(self, client, hello_project):
        source = client.post(f"/api/projects/{hello_project}/sources").json()

        response = client.put(
            f"/api/projects/{hello_project}/sources/{source['name']}/url", json={"value": "avance:behaviour/nope.csv"}
        )
        assert response.status_code == 400

    def test_renaming_the_id_renames_its_archive_and_keeps_its_content(self, client, hello_project):
        source = client.post(f"/api/projects/{hello_project}/sources").json()
        client.put(f"/api/projects/{hello_project}/files/sources/{source['name']}.csv", content=b"a,b\n1,2\n")

        response = client.put(
            f"/api/projects/{hello_project}/sources/{source['name']}/name", json={"value": "Flight Records"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["name"] == "flight_records"
        assert payload["url"] == "avance:sources/flight_records.csv"

        assert _archive_content(client, hello_project, "sources/flight_records.csv")["content"] == "a,b\n1,2\n"
        assert client.get(f"/api/projects/{hello_project}/files/sources/{source['name']}.csv").status_code == 404


class TestDeleteSource:
    def test_removes_it_and_its_archive(self, client, hello_project):
        source = client.post(f"/api/projects/{hello_project}/sources").json()

        response = client.delete(f"/api/projects/{hello_project}/sources/{source['name']}")
        assert response.status_code == 204
        assert f"{source['name']}:" not in _index_yml(client, hello_project)
        assert client.get(f"/api/projects/{hello_project}/files/sources/{source['name']}.csv").status_code == 404


class TestPutFileDirectlyToASourcesPath:
    """Regression: PUT .../files/sources/<id>.csv when that archive doesn't
    already exist yet (e.g. a source predating auto-provisioning, or any
    other reason its own archive never got created) used to fall through
    to ArchiveLayout.canonicalize_name, which had no rule for 'sources/'
    and silently rerouted a '.csv' name under behaviour/ instead — see
    layout.py's own canonicalize_name docstring on the fix."""

    def test_creates_it_at_the_exact_sources_path_given_not_rerouted_to_behaviour(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/files/sources/behaviour.csv", content=b"a,b\n1,2\n")

        assert response.status_code == 200, response.text
        assert response.json()["content"] == "a,b\n1,2\n"
        assert client.get(f"/api/projects/{hello_project}/files/sources/behaviour.csv").status_code == 200
        assert client.get(f"/api/projects/{hello_project}/files/behaviour/behaviour.csv").status_code == 404


class TestExportOmitsCache:
    """A source's own per-session read cache (cache/sessions/<id>/...,
    see tracking.sources.avance_archive) is pure runtime scratch space,
    never part of a project's own versioned definition — ProjectManager.
    export_project_zip must never include it, even though it's a real
    Archive row like any canonical file."""

    def test_downloaded_zip_excludes_every_cache_prefixed_archive(self, client, hello_project, app_db):
        revision = app_db.get_project_revision(hello_project)
        app_db.write_archive_at_revision(hello_project, "cache/sessions/1/sources/pino.csv", revision, b"scratch", "text/csv")

        download = client.get(f"/api/projects/{hello_project}")

        assert download.status_code == 200
        with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
            assert not any(name.startswith("cache/") for name in zf.namelist())


class TestSourceZipRoundTrip:
    """A source's own sources/<id>.csv archive must survive GET
    /api/projects/{id} (download) -> POST /api/projects/upload (re-import)
    with no transformation — see settings_controller.get_project's own
    docstring on that round-trip contract, which ZipImporter's SOURCES_DIR
    handling (zip_importer.py) has to uphold for this new archive kind too."""

    def test_downloading_then_reuploading_keeps_the_source_and_its_content(self, client, hello_project):
        source = client.post(f"/api/projects/{hello_project}/sources").json()
        client.put(f"/api/projects/{hello_project}/files/sources/{source['name']}.csv", content=b"city,country\nParis,France\n")
        client.post(f"/api/projects/{hello_project}/publish", json={})

        download = client.get(f"/api/projects/{hello_project}")
        assert download.status_code == 200

        # Re-uploading the exact same revision is correctly rejected as
        # "not newer" (an unrelated business rule) — bump project.revision
        # in the downloaded zip first, same as any legitimate re-import of
        # a newer copy would arrive with.
        bumped_zip = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(download.content)) as src, zipfile.ZipFile(bumped_zip, "w") as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == "index.yml":
                    data = re.sub(rb"revision:\s*\d+", b"revision: 999", data, count=1)
                dst.writestr(item, data)

        response = client.post(
            "/api/projects/upload", content=bumped_zip.getvalue(), headers={"Content-Type": "application/zip"}
        )
        assert response.status_code == 200, response.text
        assert parse_sse_result(response)["project_id"] == hello_project

        sources = client.get(f"/api/projects/{hello_project}/sources").json()["sources"]
        reimported = next(s["source"] for s in sources if s["source"]["name"] == source["name"])
        assert reimported["url"] == f"avance:sources/{source['name']}.csv"
        assert _archive_content(client, hello_project, f"sources/{source['name']}.csv")["content"] == "city,country\nParis,France\n"
