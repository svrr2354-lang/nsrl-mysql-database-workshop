-- Ready-to-run NSRL MySQL Verification and Practice Queries
-- Use in MySQL Workbench, DBeaver, or mysql CLI over the private SSM SSL tunnel.

-- 1. Confirm database, user, server version, and encrypted TLS/SSL session
SELECT DATABASE() AS current_db, CURRENT_USER() AS db_user, VERSION() AS mysql_version;
SHOW STATUS WHERE Variable_name IN ('Ssl_cipher', 'Ssl_version');

-- 2. Verify exact NSRL row counts across all four tables
SELECT 'nsrl_file' AS table_name, COUNT(*) AS row_count FROM nsrl_file
UNION ALL SELECT 'nsrl_pkg', COUNT(*) FROM nsrl_pkg
UNION ALL SELECT 'nsrl_os', COUNT(*) FROM nsrl_os
UNION ALL SELECT 'nsrl_mfg', COUNT(*) FROM nsrl_mfg;

-- 3. Confirm referential coverage (file rows without a matching package)
SELECT COUNT(*) AS file_rows_without_package
FROM nsrl_file f
WHERE NOT EXISTS (SELECT 1 FROM nsrl_pkg p WHERE p.package_id = f.package_id);

-- 4. Exact MD5 hash lookup (real NIST NSRL Modern Minimal sample record)
SELECT file_name, file_size, md5, sha1, sha256, package_name, os_name, manufacturer_name
FROM nsrl_file_details
WHERE md5 = '51580C2AEA9DE8E933C858A34502F78D';

-- 5. Filename pattern search (e.g., Windows Update .mum manifest files)
SELECT file_name, file_size, package_name, os_name
FROM nsrl_file_details
WHERE file_name LIKE '%.mum%'
ORDER BY file_size DESC
LIMIT 15;

-- 6. File size range query (files >= 1 MB)
SELECT file_name, file_size, package_name, manufacturer_name
FROM nsrl_file_details
WHERE file_size >= 1000000
ORDER BY file_size DESC
LIMIT 15;

-- 7. Top 10 packages by file count in the loaded NSRL sample
SELECT package_name, manufacturer_name, COUNT(*) AS file_count, SUM(file_size) AS total_bytes
FROM nsrl_file_details
WHERE package_name IS NOT NULL
GROUP BY package_name, manufacturer_name
ORDER BY file_count DESC
LIMIT 10;

-- 8. Check the latest Jenkins CI/CD release provenance record
SELECT source_commit, s3_bucket, s3_prefix, loaded_at
FROM workshop_release
ORDER BY id DESC
LIMIT 5;
