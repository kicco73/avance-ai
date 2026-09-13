# Route decisions

The rule these sit under is `docs/API.md`.

## `build/local-module` and `build/backend-copy` kept their verbs

They are the Build view's own step labels, "Local module" and "backend
copy". The correspondence between what a person clicks and what the
request is called is worth more here than the noun.

## `.../build/skills` became `.../build/requirements`

It is no longer the roster — that is `GET /api/skills` — but what one
project makes of it: `required`, `disabled`, `contradicted`.
