CREATE TABLE receiver_accounts (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    external_account_id TEXT NOT NULL COLLATE NOCASE,
    display_name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, external_account_id)
);

INSERT INTO receiver_accounts(
    id, channel, external_account_id, display_name, active, created_at, updated_at
)
SELECT 'acct_' || lower(hex(randomblob(16))), channel, recipient, recipient, 1,
       MIN(ingested_at), MAX(ingested_at)
FROM events
GROUP BY channel, recipient;

ALTER TABLE conversations ADD COLUMN receiver_account_id TEXT REFERENCES receiver_accounts(id);
ALTER TABLE conversations ADD COLUMN external_user_id TEXT;
ALTER TABLE conversations ADD COLUMN bot_paused INTEGER NOT NULL DEFAULT 0 CHECK(bot_paused IN (0, 1));
ALTER TABLE conversations ADD COLUMN pause_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE conversations ADD COLUMN assigned_operator TEXT;
ALTER TABLE conversations ADD COLUMN last_outbound_at TEXT;

UPDATE conversations
SET receiver_account_id = (
        SELECT ra.id
        FROM events e
        JOIN receiver_accounts ra
          ON ra.channel = e.channel
         AND ra.external_account_id = e.recipient COLLATE NOCASE
        WHERE e.conversation_id = conversations.id
        ORDER BY e.received_at DESC, e.id DESC
        LIMIT 1
    ),
    external_user_id = (
        SELECT e.sender
        FROM events e
        WHERE e.conversation_id = conversations.id
        ORDER BY e.received_at DESC, e.id DESC
        LIMIT 1
    );

CREATE UNIQUE INDEX idx_conversations_account_customer
ON conversations(channel, receiver_account_id, external_user_id)
WHERE receiver_account_id IS NOT NULL AND external_user_id IS NOT NULL;

CREATE TABLE catalog_events (
    id TEXT PRIMARY KEY,
    venue_id TEXT NOT NULL REFERENCES venues(id),
    name TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('scheduled', 'cancelled', 'completed')),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    verified_at TEXT NOT NULL,
    verified_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE catalog_offers (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES catalog_events(id),
    name TEXT NOT NULL,
    ticket_type TEXT NOT NULL,
    price_minor INTEGER CHECK(price_minor IS NULL OR price_minor >= 0),
    currency TEXT NOT NULL,
    promotion_text TEXT NOT NULL,
    conditions TEXT NOT NULL,
    availability_status TEXT NOT NULL CHECK(availability_status IN ('available', 'sold_out', 'unknown')),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    valid_from TEXT,
    valid_until TEXT,
    verified_at TEXT NOT NULL,
    verified_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE catalog_links (
    id TEXT PRIMARY KEY,
    offer_id TEXT NOT NULL REFERENCES catalog_offers(id),
    kind TEXT NOT NULL CHECK(kind IN ('purchase', 'information')),
    url TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    verified_at TEXT NOT NULL,
    verified_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_catalog_events_active_date
ON catalog_events(active, starts_at, venue_id);
CREATE INDEX idx_catalog_offers_event_active
ON catalog_offers(event_id, active, availability_status);

CREATE TABLE conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    direction TEXT NOT NULL CHECK(direction IN ('inbound', 'outbound')),
    author_type TEXT NOT NULL CHECK(author_type IN ('customer', 'bot', 'human')),
    author_id TEXT NOT NULL,
    external_message_id TEXT,
    body_text TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('received', 'pending', 'sent', 'failed', 'unknown', 'superseded')),
    source_event_id TEXT UNIQUE REFERENCES events(id),
    source_delivery_id TEXT UNIQUE,
    created_at TEXT NOT NULL,
    sent_at TEXT
);

INSERT INTO conversation_messages(
    id, conversation_id, direction, author_type, author_id, external_message_id,
    body_text, status, source_event_id, created_at
)
SELECT 'msg_' || substr(id, 5), conversation_id, 'inbound', 'customer', sender,
       external_message_id, body_text, 'received', id, received_at
FROM events
WHERE conversation_id IS NOT NULL;

CREATE INDEX idx_conversation_messages_timeline
ON conversation_messages(conversation_id, created_at, id);

ALTER TABLE actions ADD COLUMN author_type TEXT NOT NULL DEFAULT 'agent';
ALTER TABLE jobs ADD COLUMN superseded_by_job_id TEXT REFERENCES jobs(id);

CREATE TABLE agent_decisions (
    id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL UNIQUE REFERENCES actions(id),
    schema_version TEXT NOT NULL,
    decision_action TEXT NOT NULL CHECK(decision_action IN ('reply', 'ask_clarification', 'human_required', 'ignore')),
    language TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    structured INTEGER NOT NULL CHECK(structured IN (0, 1)),
    referenced_items_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE deliveries (
    id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL UNIQUE REFERENCES actions(id),
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    channel TEXT NOT NULL,
    sender_account_id TEXT NOT NULL,
    recipient_external_id TEXT NOT NULL,
    body_text TEXT NOT NULL,
    author_type TEXT NOT NULL CHECK(author_type IN ('bot', 'human')),
    author_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'sending', 'sent', 'suppressed', 'failed', 'unknown', 'superseded')),
    idempotency_key TEXT NOT NULL UNIQUE,
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    claimed_at TEXT,
    lease_expires_at TEXT,
    worker_id TEXT,
    last_error_code TEXT,
    external_message_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT
);

CREATE INDEX idx_deliveries_claim
ON deliveries(status, available_at, created_at);
CREATE INDEX idx_deliveries_conversation
ON deliveries(conversation_id, created_at DESC);

CREATE TABLE delivery_attempts (
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL REFERENCES deliveries(id),
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('sent', 'failed', 'unknown')),
    error_code TEXT,
    external_message_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(delivery_id, attempt)
);
