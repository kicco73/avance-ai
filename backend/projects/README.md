# Projects

Each subdirectory here is a project: an `index.yml` automaton definition plus
any attachment files it references, colocated together. `default/` is the
one the backend always loads at boot (see `backend/project/project_service.py`'s
`DEFAULT_PROJECT_NAME`/`PROJECTS_DIR`) — regardless of what's been uploaded via
`PUT /api/projects/{project_name}` in a previous session; there's no persistence
of "which project was last active" across a restart.

`PUT /api/projects/{project_name}` creates these directories too, in either of
two formats — `project_name` (from the request URL) becomes the directory name
either way:

- A lone `.yml`/`.yaml`: becomes `projects/<project_name>/index.yml`.
  It can't carry attachments of its own; a reference to `attachments:` will
  fail validation unless those files already exist in that directory.
- A `.zip` bundle: becomes `projects/<project_name>/` containing whatever the
  zip had at its root — exactly one `index.yml`, plus zero or more
  attachment files, flat (no subdirectories). Re-uploading a zip under an
  existing project's name replaces that directory in full.

```
projects/
└── default/
    ├── index.yml
    ├── general_prompt.txt
    ├── precontemplation_instructions.txt
    └── acute_risk_detection_instructions.txt
```

## Attachments

Files referenced by `attachments:` lists in a project's `index.yml` — under
`general_prompt`, any state (attached to its `contextual_prompt`), or
any signal (attached to its `definition`).

```yaml
general_prompt: |
  ...
attachments:
  - clinical_tone_guidelines.md

states:
  precontemplation:
    contextual_prompt: |
      ...
    attachments:
      - precontemplation_clinical_notes.md
```

### Conventions

- Paths in `attachments:` are relative to the directory holding the YAML
  file being parsed — each project's attachments live alongside its own
  `index.yml`, not in one shared location.
- Supported extensions: `.md`, `.txt`, `.csv` (sent as plain text) and `.pdf`
  (sent as base64). Anything else (`.docx`, `.xlsx`, ...) fails validation
  with an explicit error (at boot, or on upload) — no automatic conversion.
- A missing path also fails validation with an explicit error naming the
  field and file — never silently ignored.
- Attachments are read once whenever a project is loaded (at boot, or via
  `PUT /api/projects/{project_name}`) and kept in memory for that automaton's
  lifetime: editing a file here doesn't take effect until the project is
  reloaded — restart the backend, or re-upload a project referencing it.
- Scoping is strict: a chat turn only ever sees `general_prompt`'
  attachments plus the *current* state's — never another state's or a
  signal's. The signals computation call only ever sees signals'
  attachments — never a state's or `general_prompt`'.
