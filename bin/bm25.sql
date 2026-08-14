-- Okapi BM25 over the passage index, in plain SQL.
--
-- This cluster has no pg_search, and Postgres' built-in ts_rank_cd is not a
-- substitute: it has no real inverse document frequency and no document-length
-- saturation. Those are the two terms that make BM25 good at exactly what this
-- corpus needs - "PDE-260022" and "Orange Belt Trail" are rare, and a rare term
-- matching should outweigh a common one matching many times.
--
--   score(D,Q) = sum over query terms t of
--                  IDF(t) * (tf * (k1+1)) / (tf + k1*(1 - b + b*|D|/avgdl))
--   IDF(t)     = ln( (N - df + 0.5) / (df + 0.5) + 1 )
--
-- k1=1.2, b=0.75 are SQLite FTS5's defaults, kept so ranking behaviour carries
-- over from the measurements already made against FTS5 rather than silently
-- becoming a different system.
--
-- Both sides run through to_tsvector('english', ...): the analyzer that builds
-- the postings is the same one that parses the query, stemming and stopwords
-- included. Nothing here tokenises by hand, because an index and a query that
-- disagree about what a token is fail quietly and look like bad relevance.

CREATE OR REPLACE PROCEDURE bm25_rebuild()
LANGUAGE plpgsql AS $$
BEGIN
    -- The postings index is dropped first: rebuilding it in bulk afterwards is
    -- far cheaper than maintaining it across a few million inserts.
    DROP INDEX IF EXISTS passage_terms_term;
    TRUNCATE passage_terms, passage_len, term_df, bm25_stats;

    INSERT INTO passage_terms (passage_id, term, tf)
    SELECT p.id, t.lexeme,
           LEAST(COALESCE(array_length(t.positions, 1), 1), 32767)
    FROM passages p,
         LATERAL unnest(to_tsvector('english', COALESCE(p.search_text, p.text))) t;

    INSERT INTO passage_len (passage_id, len)
    SELECT passage_id, SUM(tf) FROM passage_terms GROUP BY passage_id;

    INSERT INTO term_df (term, df)
    SELECT term, COUNT(*) FROM passage_terms GROUP BY term;

    -- avgdl over documents that actually produced tokens; a passage whose text
    -- is entirely stopwords contributes nothing and is simply not findable.
    INSERT INTO bm25_stats (n_docs, avgdl)
    SELECT COUNT(*), COALESCE(AVG(len), 1) FROM passage_len;

    CREATE INDEX passage_terms_term ON passage_terms (term) INCLUDE (passage_id, tf);

    ANALYZE passage_terms;
    ANALYZE passage_len;
    ANALYZE term_df;
END $$;


CREATE OR REPLACE FUNCTION bm25(q text, lim integer DEFAULT 200)
RETURNS TABLE (passage_id integer, score double precision)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    WITH s AS (SELECT n_docs, avgdl FROM bm25_stats LIMIT 1),
         qt AS (SELECT DISTINCT lexeme FROM unnest(to_tsvector('english', q)))
    SELECT pt.passage_id,
           SUM( ln((s.n_docs - d.df + 0.5) / (d.df + 0.5) + 1)
                * (pt.tf * (1.2 + 1))
                / (pt.tf + 1.2 * (1 - 0.75 + 0.75 * pl.len / s.avgdl)) )
    FROM qt
    JOIN term_df       d  ON d.term = qt.lexeme
    JOIN passage_terms pt ON pt.term = qt.lexeme
    JOIN passage_len   pl ON pl.passage_id = pt.passage_id
    CROSS JOIN s
    GROUP BY pt.passage_id
    ORDER BY 2 DESC
    LIMIT lim;
$$;

-- Re-post a handful of documents without rebuilding five million rows.
--
-- A speaker correction changes the text of the passages it touches, and that
-- text is what is indexed - so the postings have to follow or search keeps
-- answering with the old name. Rebuilding everything for a three-utterance fix
-- is what makes people stop making the fix.
--
-- Document frequency is a global count, so it is decremented for the terms
-- these documents used to contain and incremented for the ones they contain
-- now; avgdl and n_docs are recomputed from passage_len, which is small.
CREATE OR REPLACE PROCEDURE bm25_refresh(ids integer[])
LANGUAGE plpgsql AS $$
BEGIN
    IF ids IS NULL OR array_length(ids, 1) IS NULL THEN
        RETURN;
    END IF;

    UPDATE term_df d SET df = d.df - x.n
      FROM (SELECT term, COUNT(*) AS n FROM passage_terms
             WHERE passage_id = ANY(ids) GROUP BY term) x
     WHERE d.term = x.term;
    DELETE FROM term_df WHERE df <= 0;

    DELETE FROM passage_terms WHERE passage_id = ANY(ids);
    DELETE FROM passage_len   WHERE passage_id = ANY(ids);

    INSERT INTO passage_terms (passage_id, term, tf)
    SELECT p.id, t.lexeme,
           LEAST(COALESCE(array_length(t.positions, 1), 1), 32767)
    FROM passages p,
         LATERAL unnest(to_tsvector('english', COALESCE(p.search_text, p.text))) t
    WHERE p.id = ANY(ids);

    INSERT INTO passage_len (passage_id, len)
    SELECT passage_id, SUM(tf) FROM passage_terms
     WHERE passage_id = ANY(ids) GROUP BY passage_id;

    INSERT INTO term_df (term, df)
    SELECT term, COUNT(*) FROM passage_terms
     WHERE passage_id = ANY(ids) GROUP BY term
        ON CONFLICT (term) DO UPDATE SET df = term_df.df + EXCLUDED.df;

    UPDATE bm25_stats SET
        n_docs = (SELECT COUNT(*) FROM passage_len),
        avgdl  = (SELECT COALESCE(AVG(len), 1) FROM passage_len);
END $$;
