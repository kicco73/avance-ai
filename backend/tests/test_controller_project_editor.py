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


def _archive_content(client, project_name: str, archive_name: str):
    response = client.get(f"/api/projects/{project_name}/files/{archive_name}")
    assert response.status_code == 200
    return response.json()


class TestAdd:
    def test_state_is_created_with_defaults_and_persisted_and_an_unknown_project_is_404(self, client, hello_project):
        response = client.post(f"/api/projects/{hello_project}/states")
        assert response.status_code == 200
        payload = response.json()
        assert payload["key"] == "state-0"
        assert payload["ui_label"] == "New State"
        assert payload["final"] is True
        assert payload["actions"] == []
        assert "state-0:" in _index_yml(client, hello_project)

        assert client.post("/api/projects/does-not-exist/states").status_code == 404

    def test_signal_is_created_with_defaults_and_persisted(self, client, hello_project):
        response = client.post(f"/api/projects/{hello_project}/signals")
        assert response.status_code == 200
        payload = response.json()
        assert payload["name"] == "new_signal"
        assert payload["ui_label"] == "New Signal"
        assert "new_signal:" in _index_yml(client, hello_project)

    def test_action_is_created_as_a_self_loop_on_the_given_state_and_an_unknown_state_is_400(self, client, hello_project):
        response = client.post(f"/api/projects/{hello_project}/states/Hello/actions")
        assert response.status_code == 200
        payload = response.json()
        assert payload["name"] == "action-0"
        assert payload["ui_label"] == "New Action"
        assert payload["target"] == "Hello"
        assert payload["has_trigger"] is False

        assert client.post(f"/api/projects/{hello_project}/states/does-not-exist/actions").status_code == 400


class TestPutStateField:
    def test_edits_ui_label_trimmed_history_cutoff_and_ui_description(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/states/Hello/ui-label", json={"value": "  Greeting  "})
        assert response.status_code == 200
        assert response.json()["ui_label"] == "Greeting"
        assert "Greeting" in _index_yml(client, hello_project)

        assert client.put(f"/api/projects/{hello_project}/states/Hello/history-cutoff", json={"value": True}).status_code == 200

        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/ui-description", json={"value": "A friendly greeting."}
        )
        assert response.status_code == 200
        assert response.json()["ui_description"] == "A friendly greeting."
        assert "A friendly greeting." in _index_yml(client, hello_project)


class TestPutActionField:
    def test_edits_target_ui_description_on_enter_and_trigger_and_clearing_trigger_removes_the_key(self, client, hello_project):
        client.post(f"/api/projects/{hello_project}/states")
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        base = f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}"

        response = client.put(f"{base}/target", json={"value": "state-0"})
        assert response.status_code == 200
        assert response.json()["target"] == "state-0"

        response = client.put(f"{base}/ui-description", json={"value": "A short action description."})
        assert response.status_code == 200
        assert response.json()["ui_description"] == "A short action description."
        assert "A short action description." in _index_yml(client, hello_project)

        response = client.put(f"{base}/on-enter", json={"value": "actuator.notify('Nice!', 'You reached **state B**.')"})
        assert response.status_code == 200
        assert response.json()["on-enter"] == "actuator.notify('Nice!', 'You reached **state B**.')"
        assert "on-enter:" in _index_yml(client, hello_project)

        response = client.put(f"{base}/trigger", json={"value": "True"})
        assert response.status_code == 200
        assert response.json()["has_trigger"] is True
        assert "trigger:" in _index_yml(client, hello_project)

        response = client.put(f"{base}/trigger", json={"value": ""})
        assert response.status_code == 200
        assert response.json()["has_trigger"] is False
        assert "trigger" not in _index_yml(client, hello_project)

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

    def test_env_is_set_cleared_without_leaving_an_empty_mapping_and_gated_on_declared_keys(self, client, hello_project):
        env_key = client.post(f"/api/projects/{hello_project}/env-keys").json()
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        url = f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/env"

        assert client.put(url, json={"value": {env_key["name"]: "1"}}).status_code == 200
        assert f"{env_key['name']}:" in _index_yml(client, hello_project)

        assert client.put(url, json={"value": {}}).status_code == 200
        assert _index_yml(client, hello_project).count("env:") == 1

        assert client.put(url, json={"value": {"never_declared_anywhere": "1"}}).status_code == 400


class TestPutSignalField:
    def test_edits_definition_and_ui_description_and_a_ui_label_edit_renames_the_signal(self, client, hello_project):
        signal = client.post(f"/api/projects/{hello_project}/signals").json()
        base = f"/api/projects/{hello_project}/signals/{signal['name']}"

        response = client.put(f"{base}/definition", json={"value": "a real definition"})
        assert response.status_code == 200
        assert response.json()["definition"] == "a real definition"

        response = client.put(f"{base}/ui-description", json={"value": "A short human description."})
        assert response.status_code == 200
        assert response.json()["ui_description"] == "A short human description."

        response = client.put(f"{base}/ui-label", json={"value": "Risk Level"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["name"] == "risk_level"
        assert payload["ui_label"] == "Risk Level"
        assert "risk_level:" in _index_yml(client, hello_project)


class TestPutInitAction:
    def test_target_moves_the_start_state_and_rejects_an_unknown_one(self, client, hello_project):
        response = client.put(f"/api/projects/{hello_project}/init-action/target", json={"value": "does-not-exist"})
        assert response.status_code == 400
        assert "Hello:" in _index_yml(client, hello_project)

        client.post(f"/api/projects/{hello_project}/states")
        response = client.put(f"/api/projects/{hello_project}/init-action/target", json={"value": "state-0"})
        assert response.status_code == 200
        assert response.json()["key"] == "state-0"

        assert client.delete(f"/api/projects/{hello_project}/states/Hello").status_code == 204
        assert client.delete(f"/api/projects/{hello_project}/states/state-0").status_code == 400

    def test_edits_ui_description_and_on_enter_never_reports_a_trigger_and_rejects_setting_one(self, client, hello_project):
        """The init-action is an action like any other action-field edit —
        same payload shape, same endpoint pattern, same env editing — minus
        'trigger', which AutomatonBuilder's own _build_init_action never
        reads for it (the init-action is the automaton's unconditional
        entry point, never conditionally fired)."""
        base = f"/api/projects/{hello_project}/init-action"

        response = client.put(f"{base}/ui-description", json={"value": "Where every session begins."})
        assert response.status_code == 200
        assert response.json()["ui_description"] == "Where every session begins."
        assert "Where every session begins." in _index_yml(client, hello_project)

        response = client.put(f"{base}/on-enter", json={"value": "actuator.celebrate()"})
        assert response.status_code == 200
        assert response.json()["on-enter"] == "actuator.celebrate()"

        response = client.put(f"{base}/ui-label", json={"value": "Start"})
        assert response.status_code == 200
        assert response.json()["has_trigger"] is False

        assert client.put(f"{base}/trigger", json={"value": "True"}).status_code == 400

    def test_env_is_set_cleared_without_leaving_an_empty_mapping_and_gated_on_declared_keys(self, client, hello_project):
        env_key = client.post(f"/api/projects/{hello_project}/env-keys").json()
        url = f"/api/projects/{hello_project}/init-action/env"

        assert client.put(url, json={"value": {env_key["name"]: "1"}}).status_code == 200
        assert f"{env_key['name']}:" in _index_yml(client, hello_project)

        assert client.put(url, json={"value": {}}).status_code == 200
        assert "env: {}" not in _index_yml(client, hello_project)

        assert client.put(url, json={"value": {"never_declared": "1"}}).status_code == 400


def test_every_field_endpoint_rejects_a_field_not_on_its_whitelist(client, hello_project):
    action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
    signal = client.post(f"/api/projects/{hello_project}/signals").json()
    source = client.post(f"/api/projects/{hello_project}/sources").json()

    for path, value in [
        ("states/Hello/fixed-message", "x"),
        (f"states/Hello/actions/{action['name']}/not-a-real-field", "anything"),
        (f"signals/{signal['name']}/attachments", "x"),
        ("init-action/not-a-real-field", "anything"),
        (f"sources/{source['name']}/url", "avance:behaviour/nope.csv"),
    ]:
        assert client.put(f"/api/projects/{hello_project}/{path}", json={"value": value}).status_code == 400, path


class TestReorderActions:
    def test_moves_an_action_returning_the_new_order_and_an_out_of_range_position_is_400(self, client, hello_project):
        first = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        second = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        base = f"/api/projects/{hello_project}/states/Hello/actions"

        response = client.put(f"{base}/{second['name']}/order", json={"value": 0})
        assert response.status_code == 200
        assert [a["name"] for a in response.json()] == [second["name"], first["name"]]

        assert client.put(f"{base}/{first['name']}/order", json={"value": 99}).status_code == 400


class TestDelete:
    def test_removes_a_state_action_or_signal_but_never_the_init_actions_own_target(self, client, hello_project):
        client.post(f"/api/projects/{hello_project}/states")
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        signal = client.post(f"/api/projects/{hello_project}/signals").json()

        assert client.delete(f"/api/projects/{hello_project}/states/state-0").status_code == 204
        assert "state-0:" not in _index_yml(client, hello_project)

        assert client.delete(f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}").status_code == 204
        assert action["name"] not in _index_yml(client, hello_project)
        assert "Hello:" in _index_yml(client, hello_project)

        assert client.delete(f"/api/projects/{hello_project}/signals/{signal['name']}").status_code == 204
        assert f"{signal['name']}:" not in _index_yml(client, hello_project)

        assert client.delete(f"/api/projects/{hello_project}/states/Hello").status_code == 400
        assert "Hello:" in _index_yml(client, hello_project)


class TestSources:
    def test_add_creates_an_empty_archive_per_source_suffixing_name_collisions(self, client, hello_project):
        response = client.post(f"/api/projects/{hello_project}/sources")
        assert response.status_code == 200
        payload = response.json()
        assert payload["name"] == "behaviour"
        assert payload["url"] == "avance:sources/behaviour.csv"
        assert "behaviour:" in _index_yml(client, hello_project)
        assert _archive_content(client, hello_project, "sources/behaviour.csv")["content"] == ""

        second = client.post(f"/api/projects/{hello_project}/sources").json()
        assert second["name"] == "behaviour1"
        assert second["url"] == "avance:sources/behaviour1.csv"
        assert _archive_content(client, hello_project, "sources/behaviour1.csv")["content"] == ""

    def test_driver_env_creates_an_avance_env_source_with_no_archive(self, client, hello_project):
        # An avance:env source with nothing exported is a build error (see
        # AutomatonBuilder._validate_env_sources) — an exported key has to
        # exist first, same ordering any other "declare the dependency,
        # then reference it" field in this app already requires.
        env_key = client.post(f"/api/projects/{hello_project}/env-keys").json()
        client.put(
            f"/api/projects/{hello_project}/env-keys/{env_key['name']}/ai-definition",
            json={"value": "A test variable."},
        )
        client.put(
            f"/api/projects/{hello_project}/env-keys/{env_key['name']}/ai-access", json={"value": "readonly"}
        )

        response = client.post(f"/api/projects/{hello_project}/sources?driver=env")
        assert response.status_code == 200
        payload = response.json()

        assert payload["url"] == "avance:env"
        assert client.get(f"/api/projects/{hello_project}/files/sources/{payload['name']}.csv").status_code == 404

    def test_ui_label_is_a_plain_edit_and_renaming_the_id_renames_its_archive_keeping_its_content(self, client, hello_project):
        source = client.post(f"/api/projects/{hello_project}/sources").json()
        client.put(f"/api/projects/{hello_project}/files/sources/{source['name']}.csv", content=b"a,b\n1,2\n")

        response = client.put(f"/api/projects/{hello_project}/sources/{source['name']}/ui-label", json={"value": "Flights"})
        assert response.status_code == 200
        assert response.json()["ui_label"] == "Flights"

        response = client.put(f"/api/projects/{hello_project}/sources/{source['name']}/name", json={"value": "Flight Records"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["name"] == "flight_records"
        assert payload["url"] == "avance:sources/flight_records.csv"
        assert _archive_content(client, hello_project, "sources/flight_records.csv")["content"] == "a,b\n1,2\n"
        assert client.get(f"/api/projects/{hello_project}/files/sources/{source['name']}.csv").status_code == 404

    def test_delete_removes_it_and_its_archive(self, client, hello_project):
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
