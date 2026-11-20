#!/usr/bin/env python3
"""NSRL MySQL Query System — Browser GUI for CSCI 6441 (Database Management Systems II).

Connects privately to Amazon RDS MySQL 8.4 over an SSM port-forwarding tunnel
(default 127.0.0.1:13306) with TLS verification (RDS global-bundle.pem) and
runtime credentials from AWS Secrets Manager.
"""
import json
import os
import ssl
from pathlib import Path

import boto3
import pymysql
from flask import Flask, jsonify, render_template, request

APP_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(APP_DIR / 'templates'))

_SECRET_CACHE = None


def get_db_config():
    global _SECRET_CACHE
    host = os.environ.get('DB_HOST', '127.0.0.1')
    port = int(os.environ.get('DB_PORT', '13306'))
    db_name = os.environ.get('DB_NAME', 'jj_nsrl')
    secret_arn = os.environ.get('DB_SECRET_ARN', '')
    region = os.environ.get('AWS_REGION', 'us-east-1')

    if secret_arn:
        if _SECRET_CACHE is None:
            sm = boto3.client('secretsmanager', region_name=region)
            _SECRET_CACHE = json.loads(sm.get_secret_value(SecretId=secret_arn)['SecretString'])
        user = _SECRET_CACHE['username']
        password = _SECRET_CACHE['password']
    else:
        user = os.environ.get('DB_USER', 'nsrl_admin')
        password = os.environ.get('DB_PASSWORD', '')

    return host, port, db_name, user, password


def get_connection():
    host, port, db_name, user, password = get_db_config()
    ca_path = os.environ.get('RDS_CA_BUNDLE', '')
    ssl_ctx = None
    if ca_path and not Path(ca_path).is_file():
        raise FileNotFoundError(f'RDS CA bundle not found: {ca_path}')
    if ca_path and not Path(ca_path).is_file():
        raise FileNotFoundError(f'RDS CA bundle not found: {ca_path}')
    if ca_path and Path(ca_path).exists():
        ssl_ctx = ssl.create_default_context(cafile=ca_path)
        # When tunneling via 127.0.0.1 SSM port forwarding, verify the AWS RDS CA chain (verify-ca).
        ssl_ctx.check_hostname = os.environ.get('DB_SSL_VERIFY_HOSTNAME', 'false').lower() == 'true'
        ssl_ctx.verify_mode = ssl.CERT_REQUIRED
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=db_name,
        charset='utf8mb4',
        ssl=ssl_ctx,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=15,
        read_timeout=30,
    )


def build_search_query(params):
    where = []
    args = []

    digest = (params.get('hash') or '').strip().upper()
    if digest:
        if len(digest) == 32:
            where.append('md5 = %s')
            args.append(digest)
        elif len(digest) == 40:
            where.append('sha1 = %s')
            args.append(digest)
        elif len(digest) == 64:
            where.append('sha256 = %s')
            args.append(digest)
        else:
            where.append('(md5 LIKE %s OR sha1 LIKE %s OR sha256 LIKE %s)')
            like_val = f'{digest}%'
            args.extend([like_val, like_val, like_val])

    file_name = (params.get('file_name') or '').strip()
    if file_name:
        where.append('file_name LIKE %s')
        args.append(f'%{file_name}%')

    package_name = (params.get('package_name') or '').strip()
    if package_name:
        where.append('package_name LIKE %s')
        args.append(f'%{package_name}%')

    os_name = (params.get('os_name') or '').strip()
    if os_name:
        where.append('os_name LIKE %s')
        args.append(f'%{os_name}%')

    mfg_name = (params.get('manufacturer') or '').strip()
    if mfg_name:
        where.append('manufacturer_name LIKE %s')
        args.append(f'%{mfg_name}%')

    min_size = (params.get('min_size') or '').strip()
    if min_size.isdigit():
        where.append('file_size >= %s')
        args.append(int(min_size))

    max_size = (params.get('max_size') or '').strip()
    if max_size.isdigit():
        where.append('file_size <= %s')
        args.append(int(max_size))

    limit = min(max(int(params.get('limit') or 50), 1), 200)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    sql = (
        'SELECT file_name, file_size, md5, sha1, sha256, package_id, package_name, '
        'package_version, os_name, manufacturer_name, application_type '
        f'FROM nsrl_file_details{where_sql} '
        'ORDER BY file_size DESC LIMIT %s'
    )
    count_sql = f'SELECT COUNT(*) AS total FROM nsrl_file_details{where_sql}'
    return sql, args + [limit], count_sql, list(args)


@app.route('/')
def index():
    return render_template('index.html', app_mode='Database Class GUI (CSCI 6441)')


@app.route('/healthz')
def healthz():
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW STATUS WHERE Variable_name IN ('Ssl_cipher', 'Ssl_version')")
                tls = {r['Variable_name']: r['Value'] for r in cur.fetchall()}
                cur.execute('SELECT VERSION() AS mysql_version, DATABASE() AS db_name')
                info = cur.fetchone()
        finally:
            conn.close()
        return jsonify({'status': 'healthy', 'database': info['db_name'], 'mysql_version': info['mysql_version'], 'tls': tls})
    except Exception as exc:
        return jsonify({'status': 'unhealthy', 'error': str(exc)}), 503


@app.route('/api/stats')
def api_stats():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                  (SELECT COUNT(*) FROM nsrl_file) AS files,
                  (SELECT COUNT(*) FROM nsrl_pkg) AS packages,
                  (SELECT COUNT(*) FROM nsrl_os) AS operating_systems,
                  (SELECT COUNT(*) FROM nsrl_mfg) AS manufacturers
            """)
            counts = cur.fetchone()
            cur.execute("SHOW STATUS WHERE Variable_name IN ('Ssl_cipher', 'Ssl_version')")
            tls = {r['Variable_name']: r['Value'] for r in cur.fetchall()}
            cur.execute('SELECT VERSION() AS mysql_version, DATABASE() AS db_name, @@hostname AS server_host')
            meta = cur.fetchone()
            cur.execute('SELECT source_commit, s3_bucket, s3_prefix, loaded_at FROM workshop_release ORDER BY id DESC LIMIT 1')
            release = cur.fetchone()
            if release and release.get('loaded_at'):
                release['loaded_at'] = str(release['loaded_at'])
    finally:
        conn.close()
    return jsonify({
        'counts': counts,
        'tls': tls,
        'mysql_version': meta['mysql_version'],
        'database': meta['db_name'],
        'server_host': meta['server_host'],
        'release': release,
        'connection_mode': os.environ.get('CONNECTION_MODE', 'Private SSM Tunnel + RDS SSL (verify-ca)'),
    })


@app.route('/api/search')
def api_search():
    sql, args, count_sql, count_args = build_search_query(request.args)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(count_sql, count_args)
            total = cur.fetchone()['total']
            cur.execute(sql, args)
            rows = cur.fetchall()
    finally:
        conn.close()
    return jsonify({
        'total_matches': total,
        'returned': len(rows),
        'sql_preview': sql,
        'rows': rows,
    })


if __name__ == '__main__':
    port = int(os.environ.get('GUI_PORT', '18089'))
    app.run(host='127.0.0.1', port=port, debug=False)
