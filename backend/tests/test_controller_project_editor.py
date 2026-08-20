"""index.yml structural editing endpoints — add/edit/delete/reorder
states, actions, and signals without hand-writing YAML (see
ProjectService._edit_index_yml/AutomatonYamlEditor). Every one of these
reuses PUT /api/projects/{project_name}/files/index.yml's own
validation/history/commit path — checked here by reading the persisted
YAML back via GET .../files/index.yml after each write, not by asserting
on the raw text directly (that's AutomatonYamlEditor's own test file's
job).
"""
from __future__ import annotations

import pytest

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
            json={"value": "notify('Nice!', 'You reached **state B**.')"},
        )
        assert response.status_code == 200
        assert response.json()["on-enter"] == "notify('Nice!', 'You reached **state B**.')"
        assert "on-enter:" in _index_yml(client, hello_project)

    def test_rejects_a_field_not_on_the_whitelist(self, client, hello_project):
        action = client.post(f"/api/projects/{hello_project}/states/Hello/actions").json()
        response = client.put(
            f"/api/projects/{hello_project}/states/Hello/actions/{action['name']}/not-a-real-field",
            json={"value": "anything"},
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
        # state-0 can't — the exact same guard as before, just flipped.
        assert client.delete(f"/api/projects/{hello_project}/states/Hello").status_code == 204
        assert client.delete(f"/api/projects/{hello_project}/states/state-0").status_code == 400

    def test_rejects_an_unknown_state(self, client, hello_project):
        response = client.put(
            f"/api/projects/{hello_project}/init-action/target", json={"value": "does-not-exist"}
        )
        assert response.status_code == 400
        assert "Hello:" in _index_yml(client, hello_project)


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
