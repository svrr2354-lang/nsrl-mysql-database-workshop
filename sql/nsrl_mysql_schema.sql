-- NSRL MySQL 8.4 Relational Schema (Adapted from NSRL PostgreSQL S3 Workshop)
-- Uses InnoDB, utf8mb4, explicit indexes for hash/filename/package lookups,
-- a release tracking table, and a joined view for GUI and SQL workbench queries.

DROP VIEW IF EXISTS nsrl_file_details;
DROP TABLE IF EXISTS nsrl_file;
DROP TABLE IF EXISTS nsrl_pkg;
DROP TABLE IF EXISTS nsrl_os;
DROP TABLE IF EXISTS nsrl_mfg;
DROP TABLE IF EXISTS workshop_release;

CREATE TABLE nsrl_mfg (
  manufacturer_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  PRIMARY KEY (manufacturer_id),
  INDEX idx_nsrl_mfg_name (name(128))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE nsrl_os (
  operating_system_id INT NOT NULL,
  name VARCHAR(128) NOT NULL,
  version VARCHAR(128) NOT NULL,
  manufacturer_id INT NOT NULL,
  PRIMARY KEY (operating_system_id),
  INDEX idx_nsrl_os_mfg (manufacturer_id),
  INDEX idx_nsrl_os_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE nsrl_pkg (
  id BIGINT NOT NULL AUTO_INCREMENT,
  package_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  version VARCHAR(128) NOT NULL,
  operating_system_id INT NOT NULL,
  manufacturer_id INT NOT NULL,
  language VARCHAR(128) NOT NULL,
  application_type VARCHAR(128) NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_nsrl_pkg_id (package_id),
  INDEX idx_nsrl_pkg_os (operating_system_id),
  INDEX idx_nsrl_pkg_mfg (manufacturer_id),
  INDEX idx_nsrl_pkg_name (name(128))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE nsrl_file (
  id BIGINT NOT NULL AUTO_INCREMENT,
  sha256 CHAR(64) NOT NULL,
  sha1 CHAR(40) NOT NULL,
  md5 CHAR(32) NOT NULL,
  file_name VARCHAR(512) NOT NULL,
  file_size BIGINT NOT NULL,
  package_id INT NOT NULL,
  PRIMARY KEY (id),
  INDEX idx_nsrl_file_sha256 (sha256),
  INDEX idx_nsrl_file_sha1 (sha1),
  INDEX idx_nsrl_file_md5 (md5),
  INDEX idx_nsrl_file_pkg (package_id),
  INDEX idx_nsrl_file_size (file_size),
  INDEX idx_nsrl_file_name (file_name(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE workshop_release (
  id INT NOT NULL AUTO_INCREMENT,
  source_commit CHAR(40) NOT NULL,
  s3_bucket VARCHAR(255) NOT NULL,
  s3_prefix VARCHAR(255) NOT NULL,
  loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE OR REPLACE VIEW nsrl_file_details AS
SELECT
  f.id AS file_row_id,
  f.sha256,
  f.sha1,
  f.md5,
  f.file_name,
  f.file_size,
  f.package_id,
  p.name AS package_name,
  p.version AS package_version,
  p.language,
  p.application_type,
  o.name AS os_name,
  o.version AS os_version,
  m.name AS manufacturer_name
FROM nsrl_file f
LEFT JOIN (
  -- Select one complete source row per package ID. Do not combine values
  -- from different rows when package IDs occur more than once.
  SELECT pkg.* FROM nsrl_pkg pkg
  JOIN (SELECT package_id, MIN(id) AS first_id FROM nsrl_pkg GROUP BY package_id) first_pkg
    ON pkg.id = first_pkg.first_id
) p ON p.package_id = f.package_id
LEFT JOIN nsrl_os o ON o.operating_system_id = p.operating_system_id
LEFT JOIN nsrl_mfg m ON m.manufacturer_id = p.manufacturer_id;
