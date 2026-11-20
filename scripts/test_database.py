#!/usr/bin/env python3
"""Integration test executed in GitHub Actions against a disposable MySQL 8.4 container."""
import json
import os
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]


def main():
    host = os.environ.get('MYSQL_HOST', '127.0.0.1')
    port = int(os.environ.get('MYSQL_PORT', '3306'))
    user = os.environ.get('MYSQL_USER', 'root')
    password = os.environ.get('MYSQL_PASSWORD', 'local-test-password')
    db = os.environ.get('MYSQL_DATABASE', 'ci_nsrl')

    manifest = json.loads((ROOT / 'data' / 'manifest.json').read_text())
    sys.path.insert(0, str(ROOT / 'scripts'))
    import workshop

    # Copy schema and data into a temporary stage directory matching S3 download layout
    stage = ROOT / 'artifacts' / 'ci_stage'
    stage.mkdir(parents=True, exist_ok=True)
    (stage / 'nsrl_mysql_schema.sql').write_text((ROOT / 'sql' / 'nsrl_mysql_schema.sql').read_text())
    for fname in workshop.NSRL_FILES:
        (stage / fname).write_bytes((ROOT / 'data' / fname).read_bytes())

    conn = pymysql.connect(host=host, port=port, user=user, password=password, database=db, charset='utf8mb4', autocommit=False)
    try:
        commit = 'a' * 40
        # Load twice to prove idempotency
        for attempt in (1, 2):
            counts = workshop.load_into_connection(conn, stage, commit, 'ci-bucket', f'releases/{commit}')
            assert counts['nsrl_file'] == manifest['tables']['file']['rows'], counts
            assert counts['nsrl_pkg'] == manifest['tables']['pkg']['rows'], counts
            assert counts['nsrl_os'] == manifest['tables']['os']['rows'], counts
            assert counts['nsrl_mfg'] == manifest['tables']['mfg']['rows'], counts

        with conn.cursor() as cur:
            cur.execute("SELECT file_name, package_name, os_name, manufacturer_name FROM nsrl_file_details WHERE md5 = '51580C2AEA9DE8E933C858A34502F78D'")
            row = cur.fetchone()
            assert row is not None and 'kb4345420' in row[0], row

            # Verify transaction rollback behavior
            cur.execute("INSERT INTO nsrl_mfg (manufacturer_id, name) VALUES (9999999, 'Rollback Test')")
            conn.rollback()
            cur.execute("SELECT COUNT(*) FROM nsrl_mfg WHERE manufacturer_id = 9999999")
            assert cur.fetchone()[0] == 0
        print('MySQL 8.4 CI integration test passed (idempotent load, joins, and transaction rollback verified).')
    finally:
        conn.close()


if __name__ == '__main__':
    import sys
    main()
