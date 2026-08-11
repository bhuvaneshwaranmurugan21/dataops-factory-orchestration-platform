CREATE SCHEMA IF NOT EXISTS control;
-- statement-break

CREATE TABLE IF NOT EXISTS control.publication_history (
    pipeline_id VARCHAR(128) NOT NULL,
    run_id VARCHAR(128) NOT NULL,
    plan_digest VARCHAR(80) NOT NULL,
    version BIGINT NOT NULL,
    activated_at TIMESTAMPTZ NOT NULL DEFAULT GETDATE(),
    PRIMARY KEY (pipeline_id, version),
    UNIQUE (run_id)
) DISTSTYLE ALL;
-- statement-break

CREATE TABLE IF NOT EXISTS control.active_publication (
    pipeline_id VARCHAR(128) NOT NULL,
    run_id VARCHAR(128) NOT NULL,
    plan_digest VARCHAR(80) NOT NULL,
    version BIGINT NOT NULL,
    activated_at TIMESTAMPTZ NOT NULL DEFAULT GETDATE(),
    PRIMARY KEY (pipeline_id)
) DISTSTYLE ALL;
-- statement-break

CREATE OR REPLACE PROCEDURE control.activate_publication(
    p_pipeline_id IN VARCHAR,
    p_run_id IN VARCHAR,
    p_plan_digest IN VARCHAR,
    p_expected_version IN BIGINT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_current_version BIGINT := 0;
    v_existing_digest VARCHAR(80);
BEGIN
    LOCK control.active_publication;

    SELECT MAX(plan_digest)
      INTO v_existing_digest
      FROM control.publication_history
     WHERE run_id = p_run_id;

    IF v_existing_digest IS NOT NULL THEN
        IF v_existing_digest <> p_plan_digest THEN
            RAISE EXCEPTION 'run already published with a different immutable plan';
        END IF;
        RETURN;
    END IF;

    SELECT COALESCE(MAX(version), 0)
      INTO v_current_version
      FROM control.active_publication
     WHERE pipeline_id = p_pipeline_id;

    IF v_current_version <> p_expected_version THEN
        RAISE EXCEPTION 'publication version conflict: expected %, found %',
            p_expected_version, v_current_version;
    END IF;

    INSERT INTO control.publication_history
        (pipeline_id, run_id, plan_digest, version)
    VALUES
        (p_pipeline_id, p_run_id, p_plan_digest, v_current_version + 1);

    DELETE FROM control.active_publication WHERE pipeline_id = p_pipeline_id;
    INSERT INTO control.active_publication
        (pipeline_id, run_id, plan_digest, version)
    VALUES
        (p_pipeline_id, p_run_id, p_plan_digest, v_current_version + 1);
END;
$$;
