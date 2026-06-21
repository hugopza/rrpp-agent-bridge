CREATE TABLE service_status (
    service TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    stopped_at TEXT,
    last_success_at TEXT,
    last_error_at TEXT,
    last_error_code TEXT,
    details_json TEXT NOT NULL
);

CREATE TABLE backup_records (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('manual', 'daily', 'monthly', 'pre_restore')),
    filename TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    integrity_status TEXT NOT NULL CHECK(integrity_status IN ('verified', 'failed')),
    encrypted_export INTEGER NOT NULL DEFAULT 0 CHECK(encrypted_export IN (0, 1)),
    verified_at TEXT,
    restored_at TEXT
);

CREATE INDEX idx_backup_records_kind_created ON backup_records(kind, created_at DESC);
