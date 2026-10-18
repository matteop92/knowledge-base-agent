# Configuration

`tags.yaml` is the approved tag list for the properties classifier. Draft it with
`python -m app.ingestion.vault_sync --propose-tags 50 --max-usd 0.25`, edit the names,
then set `approved: true`. The classifier may only pick tags from this list.
