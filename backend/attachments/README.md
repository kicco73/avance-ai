# Attachments

Files referenced by `attachments:` lists in `state_machine.yml` — under
`general_instructions`, any state (attached to its `contextual_prompt`), or
any signal (attached to its `ai_prompt`).

```yaml
general_instructions: |
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

## Conventions

- Paths in `attachments:` are relative to this directory.
- Supported extensions: `.md`, `.txt`, `.csv` (sent as plain text) and `.pdf`
  (sent as base64). Anything else (`.docx`, `.xlsx`, ...) fails the backend
  at startup with an explicit error — no automatic conversion.
- A missing path also fails the backend at startup with an explicit error
  naming the field and file — never silently ignored.
- Attachments are read once at startup and kept in memory, exactly like
  `state_machine.yml` itself: editing a file here requires a backend
  restart, not just a re-save.
- Scoping is strict: a chat turn only ever sees `general_instructions`'
  attachments plus the *current* state's — never another state's or a
  signal's. The signals computation call only ever sees signals'
  attachments — never a state's or `general_instructions`'.
