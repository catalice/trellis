-- The chat is working memory (her design, 2 Sep): what's visible on screen
-- should match what the bot holds verbatim. This registry records every
-- Telegram message id so a sweep can delete messages older than the context
-- window (Telegram only allows deletion within 48h of sending).
CREATE TABLE IF NOT EXISTS telegram_messages (
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS telegram_messages_sent ON telegram_messages (sent_at);
