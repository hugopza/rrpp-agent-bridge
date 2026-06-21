CREATE TABLE venues (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    default_language TEXT NOT NULL CHECK(default_language IN ('ca', 'es')),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE venue_routes (
    id TEXT PRIMARY KEY,
    venue_id TEXT NOT NULL REFERENCES venues(id),
    channel TEXT NOT NULL,
    recipient TEXT NOT NULL COLLATE NOCASE,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, recipient)
);

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    external_key TEXT NOT NULL,
    venue_id TEXT REFERENCES venues(id),
    status TEXT NOT NULL CHECK(status IN ('open', 'pending_review', 'resolved')),
    last_message_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, external_key)
);

ALTER TABLE events ADD COLUMN conversation_id TEXT REFERENCES conversations(id);

INSERT INTO conversations(id, channel, external_key, status, last_message_at, created_at, updated_at)
SELECT 'conv_' || lower(hex(randomblob(16))), channel, work_key, 'open',
       MAX(received_at), MIN(ingested_at), MAX(ingested_at)
FROM events
GROUP BY channel, work_key;

UPDATE events
SET conversation_id = (
    SELECT conversations.id
    FROM conversations
    WHERE conversations.channel = events.channel
      AND conversations.external_key = events.work_key
);

CREATE INDEX idx_events_conversation ON events(conversation_id, received_at);
CREATE INDEX idx_conversations_recent ON conversations(last_message_at DESC, id DESC);
CREATE INDEX idx_conversations_venue_status ON conversations(venue_id, status, last_message_at DESC);
CREATE INDEX idx_venue_routes_lookup ON venue_routes(channel, recipient, active);

CREATE TABLE action_reviews (
    id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL UNIQUE REFERENCES actions(id),
    kind TEXT NOT NULL CHECK(kind IN ('draft', 'escalation')),
    status TEXT NOT NULL CHECK(status IN ('pending', 'prepared', 'rejected', 'resolved')),
    current_text TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    reviewed_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE draft_revisions (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES action_reviews(id),
    version INTEGER NOT NULL,
    text TEXT NOT NULL,
    editor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(review_id, version)
);

CREATE INDEX idx_reviews_queue ON action_reviews(status, updated_at DESC, id DESC);
