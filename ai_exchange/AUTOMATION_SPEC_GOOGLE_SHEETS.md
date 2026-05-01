# UDB Daily Summary Automation (Google Sheets)

## Source
- Spreadsheet ID: `1LpC-l0AFgraofQzjHb165B_08p5Bs4q4P3-Vex9R-FY`
- Log sheet: `log`
- Summary sheet: `summary`

## Log sheet format
Header row is always:

`date_key | chat_id | author | text | message_datetime | window_start | window_end`

Bot rewrites this sheet daily and keeps only current day window.

## Summary sheet format (must be written by automation)
Header row:

`date_key | chat_id | bullet_order | bullet_text`

Rules:
1. Use only rows with today `date_key` from `log`.
2. Build summary per `chat_id`.
3. Write 4-8 bullets per chat.
4. Tone: slightly sarcastic, a bit cheeky, humorous; no toxicity, insults, discrimination.
5. Facts only from log rows; no invented details.
6. Clear sheet `summary` first, then write header + rows for current day.

## Row example
`2026_05_01 | -1002730880821 | 1 | Сегодня чат уверенно спорил о планах на выходные...`

## Operational constraints
- Do not modify `log` sheet.
- Do not append to previous days.
- Keep only current day output in `summary`.
