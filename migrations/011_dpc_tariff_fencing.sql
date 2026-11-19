-- C09 «Паркінги»: the protected write of the tariff manager (DPC lab 6, O6.2, O6.3). Owner: DPC.
--
-- tariff (lab 1, owner DPC) gets the generation and the owner of the accepted write that set the current value.
-- fence: the registered current generation of a protected resource, one row per garage: resource = 'tariff/<garage_id>'
-- (the protected record of the C09 card is the current tariff of a garage). The coordinator registers its generation
-- for every garage it manages; a test garage has its own row and never touches the rows of mall and center.
-- tariff_history: every operation of a resource in the order of its fence lock - writes accepted and rejected,
-- registrations of a generation (reason 'registered'). PUT /v1/tariff/{id} and POST /v1/tariff/generations take
-- `SELECT ... FROM fence WHERE resource = ... FOR UPDATE` first, so within a garage the order of id = the order of
-- operations, and GET /v1/history/check compares a write with the registrations before it.
--
-- reason: accepted | unfenced (FENCING=off, test garage only) | stale | unregistered | registered.

ALTER TABLE tariff
    ADD COLUMN generation bigint NOT NULL DEFAULT 0,
    ADD COLUMN owner      text NULL;

CREATE TABLE fence (
    resource   text PRIMARY KEY,
    generation bigint NOT NULL DEFAULT 0 CHECK (generation >= 0),
    owner      text NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO fence (resource, generation) SELECT 'tariff/' || garage_id, 0 FROM tariff;

CREATE TABLE tariff_history (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    garage_id      text NOT NULL REFERENCES garage (id),
    minor_per_hour bigint NULL CHECK (minor_per_hour > 0),
    owner          text NOT NULL,
    generation     bigint NOT NULL,
    accepted       boolean NOT NULL,
    reason         text NOT NULL
        CHECK (reason IN ('accepted', 'unfenced', 'stale', 'unregistered', 'registered')),
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tariff_history_garage_idx ON tariff_history (garage_id, id);
