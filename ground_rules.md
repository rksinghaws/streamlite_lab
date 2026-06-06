Ground rules — Server Agent UI

- Limit CPU summaries to 3–4 lines by default for the `ops_concise` profile.
- Always avoid showing private keys or sensitive files in UI.
- If parsing fails, show raw output inside a collapsible expander and a short error note.
- Mask long multiline secrets and replace with `***masked***` in the UI.
- Prefer concise actionable summaries; include charts only when `show_chart` is True in the active profile.
