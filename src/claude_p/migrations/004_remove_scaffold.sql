-- Remove the scaffolder feature. Purges historical scaffold runs and
-- any rows in dependent tables that referenced them. rate_limit_snapshots
-- keeps the most recent snapshot per window type, so we null out any
-- observed_run_id that points at a purged run rather than drop the snapshot.

DELETE FROM run_model_usage
WHERE run_id IN (SELECT id FROM runs WHERE trigger = 'scaffold');

DELETE FROM runs WHERE trigger = 'scaffold';

UPDATE rate_limit_snapshots
SET observed_run_id = NULL
WHERE observed_run_id IS NOT NULL
  AND observed_run_id NOT IN (SELECT id FROM runs);
