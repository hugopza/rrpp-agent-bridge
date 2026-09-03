-- ADR-0009 superseded venue-owned conversations. Clear historical assignments
-- and repair account/customer fields using SQLite insertion order, not UUIDs.
UPDATE conversations
SET venue_id = NULL,
    receiver_account_id = COALESCE((
        SELECT ra.id
        FROM events e
        JOIN receiver_accounts ra
          ON ra.channel = e.channel
         AND ra.external_account_id = e.recipient COLLATE NOCASE
        WHERE e.conversation_id = conversations.id
        ORDER BY e.rowid DESC
        LIMIT 1
    ), receiver_account_id),
    external_user_id = COALESCE((
        SELECT e.sender
        FROM events e
        WHERE e.conversation_id = conversations.id
        ORDER BY e.rowid DESC
        LIMIT 1
    ), external_user_id);
