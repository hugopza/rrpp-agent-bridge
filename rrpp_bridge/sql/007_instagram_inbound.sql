CREATE TABLE inbound_webhook_receipts (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    sanitized_payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('accepted', 'ignored')),
    accepted_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    ignored_count INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL,
    UNIQUE(provider, payload_sha256)
);

CREATE INDEX idx_webhook_receipts_recent
ON inbound_webhook_receipts(provider, received_at DESC);
