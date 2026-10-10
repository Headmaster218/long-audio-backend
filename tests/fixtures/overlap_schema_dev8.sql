-- Synthetic empty overlap schema from dev8 5ec6c9d; no user data.
BEGIN TRANSACTION;
CREATE TABLE chunks(id TEXT PRIMARY KEY,session_id TEXT);
CREATE TABLE overlap_candidate_migrations (name TEXT PRIMARY KEY);
INSERT INTO "overlap_candidate_migrations" VALUES('initial_track_links_v1');
CREATE TABLE overlap_regions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
      window_start REAL NOT NULL, local_group_id TEXT NOT NULL, overlap_group_id TEXT NOT NULL UNIQUE,
      start REAL NOT NULL,end REAL NOT NULL,support_start REAL NOT NULL,support_end REAL NOT NULL,
      status TEXT NOT NULL,reason_code TEXT NOT NULL DEFAULT 'unresolved',detected_source_count INTEGER NOT NULL,detected_speaker_local_ids_json TEXT NOT NULL,
      capacity INTEGER NOT NULL,original_mixture_retained INTEGER NOT NULL,continuity_status TEXT,shared_support_id TEXT,support_activity_json TEXT NOT NULL DEFAULT '{}',conditioning_evidence_json TEXT NOT NULL DEFAULT '[]', schema_error_count INTEGER, raw_output_sha256 TEXT, region_kind TEXT NOT NULL DEFAULT 'detected_overlap', model_label_count INTEGER,
      UNIQUE(chunk_id,window_start,local_group_id));
CREATE TABLE overlap_track_regions (
      track_id INTEGER NOT NULL REFERENCES overlap_tracks(id) ON DELETE CASCADE,
      region_id INTEGER NOT NULL REFERENCES overlap_regions(id) ON DELETE CASCADE,
      PRIMARY KEY(track_id,region_id));
CREATE TABLE overlap_tracks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,region_id INTEGER NOT NULL REFERENCES overlap_regions(id) ON DELETE CASCADE,
      chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,window_start REAL NOT NULL,
      local_source_track_id TEXT NOT NULL,source_track_id TEXT NOT NULL UNIQUE,overlap_group_id TEXT NOT NULL,
      start REAL NOT NULL,end REAL NOT NULL,support_start REAL NOT NULL,support_end REAL NOT NULL,
      text TEXT NOT NULL,raw_text TEXT NOT NULL,scope_id TEXT NOT NULL,source_slot INTEGER NOT NULL,
      identity_status TEXT NOT NULL,quality_status TEXT NOT NULL,reason_code TEXT NOT NULL DEFAULT 'unverified_candidate_text',provenance_json TEXT NOT NULL,conditioning_evidence_json TEXT NOT NULL DEFAULT '{}',
      UNIQUE(chunk_id,window_start,local_source_track_id));
CREATE INDEX overlap_regions_chunk ON overlap_regions(chunk_id,id);
CREATE INDEX overlap_tracks_region ON overlap_tracks(region_id,id);
CREATE INDEX overlap_track_regions_lookup ON overlap_track_regions(region_id,track_id);
DELETE FROM "sqlite_sequence";
COMMIT;
