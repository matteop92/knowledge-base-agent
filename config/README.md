# Configuration

`tag_aliases.yaml` maps a merged tag to the one that replaces it; cards, filters and counts show the new name.

`tags.yaml` is the approved tag list for the properties classifier. Draft it with
`python -m app.ingestion.vault_sync --propose-tags 50 --max-usd 0.25`, edit the names,
then set `approved: true`. The classifier may pick tags from this list and from tags the owner wrote in notes (minus blocked, merged and non-topic ones).
