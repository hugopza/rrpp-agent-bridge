INSERT INTO action_reviews(id, action_id, kind, status, current_text, version, reviewed_by, created_at, updated_at)
SELECT 'rev_' || lower(hex(randomblob(16))), a.id,
       CASE WHEN a.type = 'draft_reply' THEN 'draft' ELSE 'escalation' END,
       'pending',
       CASE WHEN a.type = 'draft_reply' AND json_valid(a.payload_json)
            THEN COALESCE(json_extract(a.payload_json, '$.text'), '') ELSE '' END,
       1, NULL, a.created_at, a.updated_at
FROM actions a
WHERE a.type IN ('draft_reply', 'escalate_to_owner')
  AND NOT EXISTS (SELECT 1 FROM action_reviews r WHERE r.action_id = a.id);

INSERT INTO draft_revisions(id, review_id, version, text, editor, created_at)
SELECT 'drev_' || lower(hex(randomblob(16))), r.id, 1, r.current_text, 'migration.review-backfill', r.created_at
FROM action_reviews r
WHERE r.kind = 'draft'
  AND NOT EXISTS (SELECT 1 FROM draft_revisions d WHERE d.review_id = r.id);

UPDATE actions
SET state = 'pending_review', updated_at = datetime('now')
WHERE id IN (SELECT action_id FROM action_reviews WHERE status = 'pending');

UPDATE conversations
SET status = 'pending_review', updated_at = datetime('now')
WHERE id IN (
    SELECT e.conversation_id
    FROM action_reviews r
    JOIN actions a ON a.id = r.action_id
    JOIN events e ON e.id = a.event_id
    WHERE r.status = 'pending'
);
