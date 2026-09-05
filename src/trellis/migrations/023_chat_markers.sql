-- The memory-horizon marker (her design, 5 Sep): one small message each
-- morning marks where verbatim memory begins; the previous day's marker is
-- deleted. This remembers which message is the current marker per chat.
CREATE TABLE IF NOT EXISTS chat_markers (
    chat_id BIGINT PRIMARY KEY,
    message_id BIGINT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
