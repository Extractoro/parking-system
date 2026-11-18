-- C09 «Паркінги»: results of the streaming job over barrier-events (DPC lab 5). Owner: DPC.
--
-- panel_window: one row per finalized 5-minute window of a garage (append mode of Spark). The write of a
-- micro-batch is one INSERT ... ON CONFLICT (run_id, garage_id, window_start) DO UPDATE in one transaction,
-- so a repeated micro-batch after a restart from the checkpoint overwrites the same values (O5.4).
-- run_id separates independent runs (main, experiments): each has its own topic, checkpoint and rows.
--
-- panel_run: what a run reads (topic, the JSONL file it was fed from) and with which rules (window,
-- watermark); the batch calculation of /v1/panel/batch takes the file and the watermark from here (O5.5).
--
-- panel_event: ids of the events that were counted in a finalized window (only when the job runs with
-- TRACK_EVENTS=1, experiment O5.3): the exact list of dropped events = the file minus these ids.

CREATE TABLE panel_run (
    run_id            text PRIMARY KEY,
    topic             text NOT NULL,
    events_file       text NULL,
    window_minutes    integer NOT NULL CHECK (window_minutes > 0),
    watermark_minutes integer NOT NULL CHECK (watermark_minutes >= 0),
    started_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE panel_window (
    run_id       text NOT NULL REFERENCES panel_run (run_id),
    garage_id    text NOT NULL,
    window_start timestamptz NOT NULL,
    window_end   timestamptz NOT NULL,
    entries      bigint NOT NULL CHECK (entries >= 0),
    exits        bigint NOT NULL CHECK (exits >= 0),
    batch_id     bigint NOT NULL,
    written_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, garage_id, window_start),
    CHECK (window_end > window_start)
);

CREATE TABLE panel_event (
    run_id       text NOT NULL REFERENCES panel_run (run_id),
    event_id     text NOT NULL,
    garage_id    text NOT NULL,
    window_start timestamptz NOT NULL,
    PRIMARY KEY (run_id, event_id)
);
