-- fluencytides_corpus 的建表 SQL（docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D2）。
-- Table DDL for fluencytides_corpus.
--
-- 依前綴分成五個系列：game_（遊戲內容）、nlp_（語言標注）、card_（建卡帳本）、
-- rel_（通用關係圖）、story_（劇情流程圖）。
-- game_legacy_scripts 由 rebuild.py 以 CREATE TABLE … LIKE yuzusoft.scripts 建立，
-- 結構與舊表完全相同，所以不在這裡定義。
--
-- 依外鍵順序排列；rebuild.py 依相反順序刪表。

-- ============================================================ game_

CREATE TABLE game_profiles (
  game_id        VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  title_ja_jp    VARCHAR(191) NOT NULL,
  title_zh_hant  VARCHAR(191) NULL,
  engine         VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  languages      JSON         NOT NULL,
  voice_dir      VARCHAR(191) COLLATE utf8mb4_bin NULL,
  avatar_dir     VARCHAR(191) COLLATE utf8mb4_bin NULL,
  legacy_source  VARCHAR(255) COLLATE utf8mb4_bin NULL,
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (game_id),
  UNIQUE KEY uk_legacy_source (legacy_source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE game_script_files (
  file_id        INT          NOT NULL AUTO_INCREMENT,
  game_id        VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  file_name      VARCHAR(191) COLLATE utf8mb4_bin NOT NULL,
  file_no        VARCHAR(16)  COLLATE utf8mb4_bin NOT NULL,
  title_ja_jp    VARCHAR(191) NOT NULL,
  title_zh_hant  VARCHAR(191) NULL,
  file_order     INT          NOT NULL,
  PRIMARY KEY (file_id),
  UNIQUE KEY uk_game_file (game_id, file_name),
  UNIQUE KEY uk_game_order (game_id, file_order),
  CONSTRAINT fk_files_game FOREIGN KEY (game_id) REFERENCES game_profiles (game_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE game_scenes (
  scene_id     INT          NOT NULL AUTO_INCREMENT,
  file_id      INT          NOT NULL,
  label        VARCHAR(191) COLLATE utf8mb4_bin NOT NULL,
  scene_order  INT          NOT NULL,
  PRIMARY KEY (scene_id),
  UNIQUE KEY uk_file_label (file_id, label),
  CONSTRAINT fk_scenes_file FOREIGN KEY (file_id) REFERENCES game_script_files (file_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE game_scene_links (
  link_id                INT          NOT NULL AUTO_INCREMENT,
  game_id                VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  from_scene_id          INT          NOT NULL,
  to_scene_id            INT          NULL,
  to_point               INT          NULL,
  kind                   ENUM('next','choice','jump','unlock') NOT NULL,
  option_index           TINYINT      NULL,
  option_text_ja_jp      VARCHAR(255) COLLATE utf8mb4_bin NULL,
  option_text_zh_hant    VARCHAR(255) NULL,
  condition_text_ja_jp   VARCHAR(255) NULL,
  condition_text_zh_hant VARCHAR(255) NULL,
  condition_vars         JSON         NULL,
  effects                JSON         NULL,
  raw_expression         VARCHAR(255) COLLATE utf8mb4_bin NULL,
  via                    VARCHAR(32)  COLLATE utf8mb4_bin NULL,
  evidence               ENUM('observed','probed','inferred') NOT NULL,
  note                   VARCHAR(255) NULL,
  PRIMARY KEY (link_id),
  UNIQUE KEY uk_link (from_scene_id, kind, option_index, to_scene_id),
  KEY idx_to (to_scene_id),
  CONSTRAINT fk_links_game FOREIGN KEY (game_id)       REFERENCES game_profiles (game_id),
  CONSTRAINT fk_links_from FOREIGN KEY (from_scene_id) REFERENCES game_scenes (scene_id),
  CONSTRAINT fk_links_to   FOREIGN KEY (to_scene_id)   REFERENCES game_scenes (scene_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE game_speakers (
  speaker_id       INT          NOT NULL AUTO_INCREMENT,
  game_id          VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  name_ja_jp       VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  name_zh_hant     VARCHAR(64)  NULL,
  name_zh_hant_src ENUM('game','manual') NULL,
  voice_prefix     VARCHAR(32)  COLLATE utf8mb4_bin NULL,
  avatar_file      VARCHAR(64)  NULL,
  sort_order       INT          NOT NULL DEFAULT 0,
  PRIMARY KEY (speaker_id),
  UNIQUE KEY uk_game_name (game_id, name_ja_jp),
  CONSTRAINT fk_speakers_game FOREIGN KEY (game_id) REFERENCES game_profiles (game_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE game_lines (
  id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  game_id            VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  scene_id           INT          NOT NULL,
  seq                INT          NOT NULL,
  story_order        INT          NOT NULL,
  kind               ENUM('line','choice','bgv') NOT NULL DEFAULT 'line',
  point              INT          NULL,
  chapter_label      VARCHAR(64)  COLLATE utf8mb4_bin NULL,
  speaker_id         INT          NULL,
  text_ja_jp         VARCHAR(1024) COLLATE utf8mb4_bin NOT NULL,
  text_zh_hant       VARCHAR(1024) NULL,
  ruby_ja_jp         VARCHAR(1024) COLLATE utf8mb4_bin NULL,
  voice_file         VARCHAR(64)  COLLATE utf8mb4_bin NULL,
  is_h               TINYINT(1)   NOT NULL DEFAULT 0,
  h_label            VARCHAR(64)  COLLATE utf8mb4_bin NULL,
  thumb              CHAR(16)     COLLATE utf8mb4_bin NULL,
  bgm_detect         VARCHAR(16)  NULL,
  bgm_may_change     TINYINT(1)   NOT NULL DEFAULT 0,
  attrs              JSON         NULL,
  status             ENUM('active','inactive') NOT NULL DEFAULT 'active',
  in_corpus          TINYINT(1)   NOT NULL DEFAULT 1,
  origin             ENUM('legacy','restored','game_only') NOT NULL,
  anchor_method      VARCHAR(16)  COLLATE utf8mb4_bin NULL,
  anchor_verified_at DATETIME     NULL,
  created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_scene_seq (scene_id, seq),
  UNIQUE KEY uk_scene_point (scene_id, point),
  UNIQUE KEY uk_game_order (game_id, story_order),
  KEY idx_voice (voice_file),
  KEY idx_thumb (thumb),
  KEY idx_game_chapter (game_id, chapter_label),
  CONSTRAINT fk_lines_game    FOREIGN KEY (game_id)    REFERENCES game_profiles (game_id),
  CONSTRAINT fk_lines_scene   FOREIGN KEY (scene_id)   REFERENCES game_scenes (scene_id),
  CONSTRAINT fk_lines_speaker FOREIGN KEY (speaker_id) REFERENCES game_speakers (speaker_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE game_bgm_tracks (
  track_id       INT          NOT NULL AUTO_INCREMENT,
  game_id        VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  code           VARCHAR(32)  COLLATE utf8mb4_bin NOT NULL,
  archive_index  INT          NULL,
  gallery_no     INT          NULL,
  title_ja_jp    VARCHAR(191) NULL,
  title_zh_hant  VARCHAR(191) NULL,
  PRIMARY KEY (track_id),
  UNIQUE KEY uk_game_code (game_id, code),
  CONSTRAINT fk_bgm_game FOREIGN KEY (game_id) REFERENCES game_profiles (game_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE game_line_bgm (
  line_id   BIGINT UNSIGNED NOT NULL,
  ord       TINYINT      NOT NULL,
  track_id  INT          NOT NULL,
  PRIMARY KEY (line_id, ord),
  KEY idx_track (track_id),
  CONSTRAINT fk_lbgm_line  FOREIGN KEY (line_id)  REFERENCES game_lines (id) ON DELETE CASCADE,
  CONSTRAINT fk_lbgm_track FOREIGN KEY (track_id) REFERENCES game_bgm_tracks (track_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE game_line_revisions (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  line_id     BIGINT UNSIGNED NOT NULL,
  field       VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  old_value   TEXT         NULL,
  new_value   TEXT         NULL,
  reason      VARCHAR(255) NOT NULL,
  evidence    VARCHAR(255) NULL,
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_line (line_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================ nlp_

CREATE TABLE nlp_line_terms (
  id               BIGINT          NOT NULL AUTO_INCREMENT,
  game_id          VARCHAR(64)     COLLATE utf8mb4_bin NOT NULL,
  line_id          BIGINT UNSIGNED NOT NULL,
  term_key         VARCHAR(255)    COLLATE utf8mb4_bin NOT NULL,
  term_type        ENUM('grammar','verb','noun','adjective_i','adjective_na','adverb') NOT NULL,
  frequency_score  TINYINT UNSIGNED NOT NULL,
  model_name       VARCHAR(255)    NOT NULL DEFAULT '',
  PRIMARY KEY (id),
  KEY idx_term_game (term_key, game_id),
  KEY idx_term_type (term_type),
  KEY idx_score (frequency_score),
  KEY idx_line (line_id),
  CONSTRAINT fk_terms_game FOREIGN KEY (game_id) REFERENCES game_profiles (game_id),
  CONSTRAINT fk_terms_line FOREIGN KEY (line_id) REFERENCES game_lines (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE nlp_line_readings (
  line_id       BIGINT UNSIGNED NOT NULL,
  verb_surface  VARCHAR(32)     NOT NULL,
  reading       VARCHAR(32)     NOT NULL,
  llm_model     VARCHAR(255)    NULL,
  created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (line_id, verb_surface),
  CONSTRAINT fk_readings_line FOREIGN KEY (line_id) REFERENCES game_lines (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================ card_

CREATE TABLE card_gen_log (
  id               INT             NOT NULL AUTO_INCREMENT,
  game_id          VARCHAR(64)     COLLATE utf8mb4_bin NOT NULL,
  line_id          BIGINT UNSIGNED NOT NULL,
  term_key         VARCHAR(255)    COLLATE utf8mb4_bin NOT NULL,
  card_type        VARCHAR(32)     COLLATE utf8mb4_bin NOT NULL,
  master_note_id   BIGINT          NOT NULL,
  context_note_id  BIGINT          NULL,
  cloze_note_id    BIGINT          NULL,
  is_deleted       TINYINT(1)      NOT NULL DEFAULT 0,
  delete_count     INT             NOT NULL DEFAULT 0,
  failure_count    INT             NOT NULL DEFAULT 0,
  llm_model        VARCHAR(255)    NULL,
  created_at       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_line_term_type (line_id, term_key, card_type),
  KEY idx_term (term_key),
  KEY idx_master (master_note_id),
  KEY idx_game_type (game_id, card_type),
  CONSTRAINT fk_cgl_game FOREIGN KEY (game_id) REFERENCES game_profiles (game_id),
  CONSTRAINT fk_cgl_line FOREIGN KEY (line_id) REFERENCES game_lines (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================ rel_

CREATE TABLE rel_entity_types (
  entity_type    VARCHAR(32)  COLLATE utf8mb4_bin NOT NULL,
  name_ja_jp     VARCHAR(64)  NOT NULL,
  name_zh_hant   VARCHAR(64)  NOT NULL,
  source         VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  id_kind        ENUM('int','string') NOT NULL DEFAULT 'int',
  PRIMARY KEY (entity_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE rel_types (
  relation_type   VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  owner           VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  name_ja_jp      VARCHAR(64)  NOT NULL,
  name_zh_hant    VARCHAR(64)  NOT NULL,
  src_types       JSON         NOT NULL,
  dst_types       JSON         NOT NULL,
  directed        TINYINT(1)   NOT NULL DEFAULT 1,
  inverse_type    VARCHAR(64)  COLLATE utf8mb4_bin NULL,
  style           JSON         NULL,
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (relation_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE rel_links (
  relation_id    BIGINT       NOT NULL AUTO_INCREMENT,
  relation_type  VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  src_type       VARCHAR(32)  COLLATE utf8mb4_bin NOT NULL,
  src_id         VARCHAR(64)  COLLATE utf8mb4_bin NULL,
  src_label      VARCHAR(200) NULL,
  dst_type       VARCHAR(32)  COLLATE utf8mb4_bin NOT NULL,
  dst_id         VARCHAR(64)  COLLATE utf8mb4_bin NULL,
  dst_label      VARCHAR(200) NULL,
  attrs          JSON         NULL,
  status         ENUM('active','deleted') NOT NULL DEFAULT 'active',
  created_by     VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (relation_id),
  KEY idx_src (src_type, src_id, relation_type, status),
  KEY idx_dst (dst_type, dst_id, relation_type, status),
  KEY idx_type (relation_type),
  CONSTRAINT fk_rel_type FOREIGN KEY (relation_type) REFERENCES rel_types (relation_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================ story_

CREATE TABLE story_builds (
  build_id        BIGINT       NOT NULL AUTO_INCREMENT,
  game_id         VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  status          ENUM('importing','ready','active','retired') NOT NULL,
  schema_version  INT          NOT NULL,
  extractor       VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  source_hash     CHAR(64)     COLLATE utf8mb4_bin NOT NULL,
  config          JSON         NOT NULL,
  graph           JSON         NOT NULL,
  stats           JSON         NOT NULL,
  note            VARCHAR(500) NULL,
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  activated_at    DATETIME     NULL,
  PRIMARY KEY (build_id),
  UNIQUE KEY uk_game_hash (game_id, source_hash),
  KEY idx_game_status (game_id, status),
  CONSTRAINT fk_builds_game FOREIGN KEY (game_id) REFERENCES game_profiles (game_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE story_node_lines (
  build_id  BIGINT          NOT NULL,
  node_key  VARCHAR(191)    COLLATE utf8mb4_bin NOT NULL,
  seq       INT             NOT NULL,
  line_id   BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (build_id, node_key, seq),
  UNIQUE KEY uk_build_line (build_id, line_id),
  CONSTRAINT fk_nodelines_build FOREIGN KEY (build_id) REFERENCES story_builds (build_id) ON DELETE CASCADE,
  CONSTRAINT fk_nodelines_line  FOREIGN KEY (line_id)  REFERENCES game_lines (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
