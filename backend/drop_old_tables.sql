-- OPTIONAL cleanup. Removes tables created by the earlier "VocalForLocal" version
-- that urSarthi does not use. Your users and login tables are NOT touched.
-- Run in psql:  \i drop_old_tables.sql
DROP TABLE IF EXISTS chunk_vectors, chunk_embeddings, knowledge_chunks, knowledge_documents,
  kb_meta, saved_destinations, user_interactions, user_preferences, messages, conversations,
  itineraries, destinations CASCADE;
