"""Layer 1 — FPL data pipeline: API client, DB schema, and the sync job.

Only the data layer lives here. Downstream layers (engine/, graph/, writer/,
api/, web/) consume this layer's contracts — the DB tables in `models` and the
`ep_scores` rows produced by the Layer 2 engine — never its internals.
"""
