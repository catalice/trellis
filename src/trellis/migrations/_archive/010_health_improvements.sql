ALTER TABLE health_self_reports
    ADD COLUMN IF NOT EXISTS soreness_score SMALLINT CHECK (soreness_score BETWEEN 1 AND 10);
