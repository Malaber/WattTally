# WattTally contributor guide

- All future changes must use a feature branch and pull request. Direct main push
  was authorized only for the initial release.
- Make focused commits as work reaches coherent, verified milestones.
- Home Assistant access must remain read-only. Never call service or control APIs.
- Authentication is passkey-only. Public signup must not grant household access
  or administrator rights without approval.
- Never commit .env, HA tokens, local databases, raw charging data or invite links.
- Keep employer exports free of solar/grid cost details; use the agreed rate for
  all reimbursable energy. Preserve house-first solar allocation for actual costs.
- Validate Python changes with ruff and pytest; authentication/frontend changes
  also require scripts/browser_smoke.py with its isolated test database.
- Never reset or modify the user's Codex usage limits.
