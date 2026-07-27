# Models

Each subdirectory here is a model: an `index.yml` automaton definition plus
any attachment files it references, colocated together. `default/` is the
one the backend always loads at boot (see `backend/main.py`'s
`DEFAULT_MODEL_PATH`) — regardless of what's been uploaded via
`POST /api/model/upload` in a previous session; there's no persistence of
"which model was last active" across a restart.

`POST /api/model/upload` creates these directories too, in either of two
formats — the uploaded file's name (without extension) becomes the
directory name either way:

- A lone `.yml`/`.yaml`: `model_example.yml` → `models/model_example/index.yml`.
  It can't carry attachments of its own; a reference to `attachments:` will
  fail validation unless those files already exist in that directory.
- A `.zip` bundle: `bundle.zip` → `models/bundle/` containing whatever the
  zip had at its root — exactly one `index.yml`, plus zero or more
  attachment files, flat (no subdirectories). Re-uploading a zip under an
  existing model's name replaces that directory in full.

```
models/
└── default/
    ├── index.yml
    ├── general_prompt.txt
    ├── precontemplation_instructions.txt
    └── acute_risk_detection_instructions.txt
```

## Attachments

Files referenced by `attachments:` lists in a model's `index.yml` — under
`general_prompt`, any state (attached to its `contextual_prompt`), or
any signal (attached to its `ai_prompt`).

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
  file being parsed — each model's attachments live alongside its own
  `index.yml`, not in one shared location.
- Supported extensions: `.md`, `.txt`, `.csv` (sent as plain text) and `.pdf`
  (sent as base64). Anything else (`.docx`, `.xlsx`, ...) fails validation
  with an explicit error (at boot, or on upload) — no automatic conversion.
- A missing path also fails validation with an explicit error naming the
  field and file — never silently ignored.
- Attachments are read once whenever a model is loaded (at boot, or via
  `POST /api/model/upload`) and kept in memory for that automaton's
  lifetime: editing a file here doesn't take effect until the model is
  reloaded — restart the backend, or re-upload a model referencing it.
- Scoping is strict: a chat turn only ever sees `general_prompt`'
  attachments plus the *current* state's — never another state's or a
  signal's. The signals computation call only ever sees signals'
  attachments — never a state's or `general_prompt`'.
