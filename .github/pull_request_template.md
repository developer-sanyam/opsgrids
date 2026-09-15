<!--
Every PR closes exactly one card. If you are closing two, open two PRs.
Delete nothing from this template — leave the unchecked boxes visible so the reviewer
can see what was and was not done.
-->

## Card

<!-- e.g. A1 — see docs/onboarding/cards-dev-a-lead.md -->

**Card:** 
**What it asked for, in one sentence:** 

## DONE WHEN — output

<!--
Paste the real terminal output of every command in the card's "DONE WHEN" block.
Not a summary, not "all passed". The actual output. A PR without this is not ready
for review and will be sent back unread.
-->

```

```

## Checklist

- [ ] I changed **only** the files the card listed under "FILES YOU WILL TOUCH".
- [ ] I did not invent anything the card did not specify — no extra column, permission,
      endpoint, prop or dependency.
- [ ] Every command in "DONE WHEN" passes, and its output is pasted above.
- [ ] My commit message does not mention Claude, Claude Code, or any AI assistant, and
      carries no co-author trailer.
- [ ] This PR targets `development`, not `main`.

### Backend PRs only

- [ ] `app/schemas.py` or `app/catalogue.py` changed → I ran `make openapi` and the
      regenerated files under `libs/contracts/` are committed in this PR.
- [ ] Every new endpoint has a negative-authz test: 401 unauthenticated, 403 without the
      permission, 402 without the product entitlement, 403 for an out-of-scope write.
- [ ] Every read goes through `app/selectors.py` and filters on `organization_id`.
- [ ] A new migration carries `# pragma: allowlist secret` on its `revision` and
      `down_revision` lines.

### Frontend PRs only

- [ ] Every list has all four states: loading, error, empty, full.
- [ ] I did not copy a component out of `@opsgrids/ui` into this app.
- [ ] No `any`, and no `!` used to silence the compiler.

## Anything I was unsure about

<!--
List every point where you nearly guessed. This is the most useful section in the
template — it tells the reviewer which card to rewrite. "Nothing" is a valid answer.
-->
