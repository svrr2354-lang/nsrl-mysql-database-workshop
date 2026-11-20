#!/usr/bin/env python3
"""Jenkins CI/CD pipeline helper for the NSRL MySQL 8.4 Database Workshop.

Handles parameter validation, CloudFormation deployment, versioned S3 release
publishing and SHA-256 verified extraction, TLS-encrypted MySQL loading via
AWS Secrets Manager, verification, Multi-AZ standby failover, and snapshot DR.
No secret values are ever printed or archived.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import time
from pathlib import Path

import boto3
import pymysql

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts'
RDS_CA_BUNDLE = os.environ.get('RDS_CA_BUNDLE', '/opt/rds/global-bundle.pem')

NSRL_FILES = ['manifest.json', 'mfg.csv', 'os.csv', 'pkg.csv', 'file.csv']


def validate(values):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{1,30}', values['DB_NAME']):
        raise ValueError('DB_NAME must start with a letter and use letters, digits, or underscores (e.g., jj_nsrl)')
    if not 20 <= int(values['STORAGE_GB']) <= 100:
        raise ValueError('STORAGE_GB must be between 20 and 100 GiB')
    if values['DB_INSTANCE_CLASS'] not in ['db.t3.micro', 'db.t3.small', 'db.t3.medium']:
        raise ValueError('Unsupported DB_INSTANCE_CLASS')
    for key, prefix in [('VPC_ID', 'vpc-'), ('JENKINS_SECURITY_GROUP_ID', 'sg-')]:
        if not re.fullmatch(prefix + r'[0-9a-f]{8,17}', values[key]):
            raise ValueError('Invalid ' + key)
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,254}', values['DB_SUBNET_GROUP']):
        raise ValueError('Invalid DB_SUBNET_GROUP name')
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9-]{0,99}', values['STACK_NAME']):
        raise ValueError('Invalid STACK_NAME')


def aws_session():
    return boto3.Session(region_name=os.environ.get('AWS_REGION', 'us-east-1'))


def stack_outputs():
    return json.loads((OUT / 'stack-outputs.json').read_text())


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def save_artifact(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=2, default=str) + '\n')


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def get_db_credentials(secret_arn: str) -> dict:
    sm = aws_session().client('secretsmanager')
    return json.loads(sm.get_secret_value(SecretId=secret_arn)['SecretString'])


def connect_mysql(host: str, db_name: str, user: str, password: str, port: int = 3306, verify_hostname: bool = True):
    ctx = ssl.create_default_context(cafile=RDS_CA_BUNDLE)
    ctx.check_hostname = verify_hostname
    ctx.verify_mode = ssl.CERT_REQUIRED
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=db_name,
        charset='utf8mb4',
        ssl=ctx,
        connect_timeout=20,
        read_timeout=120,
        write_timeout=120,
        autocommit=False,
    )


def preflight():
    validate(os.environ)
    s = aws_session()
    ec2 = s.client('ec2')
    rds = s.client('rds')
    subnet = rds.describe_db_subnet_groups(DBSubnetGroupName=os.environ['DB_SUBNET_GROUP'])['DBSubnetGroups'][0]
    if subnet['VpcId'] != os.environ['VPC_ID']:
        raise ValueError('DB subnet group belongs to a different VPC')
    azs = sorted({x['SubnetAvailabilityZone']['Name'] for x in subnet['Subnets']})
    if len(azs) < 2:
        raise ValueError('DB subnet group must span at least two Availability Zones for RDS Multi-AZ')
    sg = ec2.describe_security_groups(GroupIds=[os.environ['JENKINS_SECURITY_GROUP_ID']])['SecurityGroups'][0]
    if sg['VpcId'] != os.environ['VPC_ID']:
        raise ValueError('Jenkins security group belongs to a different VPC')
    identity = s.client('sts').get_caller_identity()
    cf = s.client('cloudformation')
    try:
        stack = cf.describe_stacks(StackName=os.environ['STACK_NAME'])['Stacks'][0]
    except cf.exceptions.ClientError as exc:
        if 'does not exist' not in str(exc):
            raise
    else:
        prior = {x['ParameterKey']: x['ParameterValue'] for x in stack['Parameters']}
        if prior.get('DBName') != os.environ['DB_NAME']:
            raise ValueError('Existing stack belongs to a different DB_NAME; use a new STACK_NAME')
        if int(os.environ['STORAGE_GB']) < int(prior.get('AllocatedStorage', '20')):
            raise ValueError('RDS storage cannot shrink; keep or increase STORAGE_GB')
        if prior.get('VpcId') != os.environ['VPC_ID'] or prior.get('DBSubnetGroupName') != os.environ['DB_SUBNET_GROUP']:
            raise ValueError('Existing database network cannot be replaced in place; use a new STACK_NAME')
    save_artifact('preflight.json', {
        'account': identity['Account'],
        'region': s.region_name,
        'db_name': os.environ['DB_NAME'],
        'multi_az': str(os.environ.get('MULTI_AZ', 'true')).lower(),
        'vpc': subnet['VpcId'],
        'subnet_group': subnet['DBSubnetGroupName'],
        'subnet_azs': azs,
        'source_commit': os.environ.get('GIT_COMMIT', 'local'),
    })
    print(f"Preflight passed: VPC {subnet['VpcId']}, AZs {azs}, MultiAZ={os.environ.get('MULTI_AZ', 'true')}.")


def deploy():
    p = os.environ
    multi_az = 'true' if str(p.get('MULTI_AZ', 'true')).lower() == 'true' else 'false'
    template_path = ROOT / 'infra' / 'mysql-rds.yaml'
    args = [
        'aws', 'cloudformation', 'deploy',
        '--region', aws_session().region_name,
        '--stack-name', p['STACK_NAME'],
        '--template-file', str(template_path),
        '--no-fail-on-empty-changeset',
        '--parameter-overrides',
        f"DBName={p['DB_NAME']}",
        f"AllocatedStorage={p['STORAGE_GB']}",
        f"DBInstanceClass={p['DB_INSTANCE_CLASS']}",
        f"MultiAZ={multi_az}",
        f"VpcId={p['VPC_ID']}",
        f"DBSubnetGroupName={p['DB_SUBNET_GROUP']}",
        f"JenkinsSecurityGroupId={p['JENKINS_SECURITY_GROUP_ID']}",
    ]
    run(args)
    stack = aws_session().client('cloudformation').describe_stacks(StackName=p['STACK_NAME'])['Stacks'][0]
    out = {x['OutputKey']: x['OutputValue'] for x in stack['Outputs']}
    save_artifact('stack-outputs.json', out)
    print(f"CloudFormation complete. Private RDS MySQL ({out['DBIdentifier']}) and S3 bucket ({out['ScriptsBucketName']}) ready.")


def publish():
    s3 = aws_session().client('s3')
    out = stack_outputs()
    commit = os.environ['GIT_COMMIT']
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('GIT_COMMIT must be a 40-character Git SHA')
    bucket = out['ScriptsBucketName']
    prefix = f'releases/{commit}'
    objects = {}

    # Publish schema SQL
    schema_path = ROOT / 'sql' / 'nsrl_mysql_schema.sql'
    schema_sha = sha256_file(schema_path)
    schema_key = f'{prefix}/nsrl_mysql_schema.sql'
    res = s3.put_object(
        Bucket=bucket, Key=schema_key, Body=schema_path.read_bytes(),
        ServerSideEncryption='AES256', ContentType='text/plain',
        Metadata={'sha256': schema_sha, 'source-commit': commit},
    )
    objects['nsrl_mysql_schema.sql'] = {'key': schema_key, 'version_id': res['VersionId'], 'sha256': schema_sha}

    # Publish NSRL data files and manifest
    for fname in NSRL_FILES:
        fpath = ROOT / 'data' / fname
        fsha = sha256_file(fpath)
        fkey = f'{prefix}/data/{fname}'
        ctype = 'application/json' if fname.endswith('.json') else 'text/csv'
        res = s3.put_object(
            Bucket=bucket, Key=fkey, Body=fpath.read_bytes(),
            ServerSideEncryption='AES256', ContentType=ctype,
            Metadata={'sha256': fsha, 'source-commit': commit},
        )
        objects[fname] = {'key': fkey, 'version_id': res['VersionId'], 'sha256': fsha}

    save_artifact('s3-release.json', {
        'bucket': bucket,
        'prefix': prefix,
        'source_commit': commit,
        'objects': objects,
    })
    print(f"Published MySQL schema and 5 NSRL dataset files privately to s3://{bucket}/{prefix}/ (versioned, SSE-S3).")


def download():
    release = json.loads((OUT / 's3-release.json').read_text())
    dl_dir = OUT / 's3_downloaded'
    dl_dir.mkdir(parents=True, exist_ok=True)
    region = aws_session().region_name
    for name, meta in release['objects'].items():
        dest = dl_dir / name
        run([
            'aws', 's3api', 'get-object',
            '--region', region,
            '--bucket', release['bucket'],
            '--key', meta['key'],
            '--version-id', meta['version_id'],
            str(dest),
        ], stdout=subprocess.DEVNULL)
        actual = sha256_file(dest)
        if actual != meta['sha256']:
            raise RuntimeError(f'SHA256 mismatch for {name}: expected {meta["sha256"]}, got {actual}')
    print('Downloaded exact S3 object versions for schema and NSRL CSVs and verified all SHA-256 checksums.')


def _batch_insert(cur, sql_stmt, csv_path: Path, row_fn, batch_size: int = 2000):
    batch = []
    total = 0
    with csv_path.open('r', newline='', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            batch.append(row_fn(row))
            if len(batch) >= batch_size:
                cur.executemany(sql_stmt, batch)
                total += len(batch)
                batch.clear()
        if batch:
            cur.executemany(sql_stmt, batch)
            total += len(batch)
    return total


def load_into_connection(conn, dl_dir: Path, source_commit: str, s3_bucket: str, s3_prefix: str):
    schema_sql = (dl_dir / 'nsrl_mysql_schema.sql').read_text(encoding='utf-8')
    with conn.cursor() as cur:
        for stmt in [s.strip() for s in schema_sql.split(';') if s.strip()]:
            cur.execute(stmt)
        conn.commit()

        mfg_n = _batch_insert(
            cur,
            'INSERT INTO nsrl_mfg (manufacturer_id, name) VALUES (%s, %s)',
            dl_dir / 'mfg.csv',
            lambda r: (int(r['manufacturer_id']), r['name']),
        )
        os_n = _batch_insert(
            cur,
            'INSERT INTO nsrl_os (operating_system_id, name, version, manufacturer_id) VALUES (%s, %s, %s, %s)',
            dl_dir / 'os.csv',
            lambda r: (int(r['operating_system_id']), r['name'], r['version'], int(r['manufacturer_id'])),
        )
        pkg_n = _batch_insert(
            cur,
            'INSERT INTO nsrl_pkg (package_id, name, version, operating_system_id, manufacturer_id, language, application_type) VALUES (%s, %s, %s, %s, %s, %s, %s)',
            dl_dir / 'pkg.csv',
            lambda r: (int(r['package_id']), r['name'], r['version'], int(r['operating_system_id']), int(r['manufacturer_id']), r['language'], r['application_type']),
        )
        file_n = _batch_insert(
            cur,
            'INSERT INTO nsrl_file (sha256, sha1, md5, file_name, file_size, package_id) VALUES (%s, %s, %s, %s, %s, %s)',
            dl_dir / 'file.csv',
            lambda r: (r['sha256'], r['sha1'], r['md5'], r['file_name'], int(r['file_size']), int(r['package_id'])),
        )
        cur.execute(
            'INSERT INTO workshop_release (source_commit, s3_bucket, s3_prefix) VALUES (%s, %s, %s)',
            (source_commit, s3_bucket, s3_prefix),
        )
        conn.commit()
    return {'nsrl_mfg': mfg_n, 'nsrl_os': os_n, 'nsrl_pkg': pkg_n, 'nsrl_file': file_n}


def load():
    out = stack_outputs()
    release = json.loads((OUT / 's3-release.json').read_text())
    secret = get_db_credentials(out['AdminSecretArn'])
    dl_dir = OUT / 's3_downloaded'
    conn = connect_mysql(out['DBEndpoint'], out['DBName'], secret['username'], secret['password'])
    try:
        counts = load_into_connection(conn, dl_dir, release['source_commit'], release['bucket'], release['prefix'])
    finally:
        conn.close()
    print(f'Loaded verified S3 NSRL dataset into MySQL 8.4 over TLS: {counts}')


def verify_connection(conn, expected_manifest: dict, expected_commit: str):
    counts = {}
    with conn.cursor() as cur:
        for short_name, tbl in [('file', 'nsrl_file'), ('pkg', 'nsrl_pkg'), ('os', 'nsrl_os'), ('mfg', 'nsrl_mfg')]:
            cur.execute(f'SELECT COUNT(*) FROM {tbl}')
            actual = cur.fetchone()[0]
            expected = expected_manifest['tables'][short_name]['rows']
            counts[tbl] = actual
            if actual != expected:
                raise RuntimeError(f'{tbl}: expected {expected} rows, found {actual}')

        cur.execute('SELECT COUNT(*) FROM nsrl_file f WHERE NOT EXISTS (SELECT 1 FROM nsrl_pkg p WHERE p.package_id = f.package_id)')
        orphans = cur.fetchone()[0]

        cur.execute("SHOW STATUS WHERE Variable_name IN ('Ssl_cipher', 'Ssl_version')")
        tls_status = dict(cur.fetchall())
        if not tls_status.get('Ssl_cipher'):
            raise RuntimeError('MySQL connection is not encrypted with SSL/TLS')

        cur.execute('SELECT VERSION(), DATABASE(), CURRENT_USER()')
        mysql_version, db_name, db_user = cur.fetchone()

        cur.execute('SELECT source_commit, s3_bucket, s3_prefix, loaded_at FROM workshop_release ORDER BY id DESC LIMIT 1')
        rel = cur.fetchone()
        if rel[0] != expected_commit:
            raise RuntimeError(f'Loaded source_commit mismatch: {rel[0]} != {expected_commit}')

        cur.execute(
            "SELECT file_name, file_size, md5, package_name, os_name, manufacturer_name "
            "FROM nsrl_file_details WHERE md5 = '51580C2AEA9DE8E933C858A34502F78D' LIMIT 1"
        )
        sample_md5 = cur.fetchone()

        cur.execute(
            "SELECT file_name, file_size, package_name, os_name, manufacturer_name "
            "FROM nsrl_file_details ORDER BY file_size DESC LIMIT 5"
        )
        largest_files = [list(r) for r in cur.fetchall()]

    return {
        'database': db_name,
        'db_user': db_user,
        'mysql_version': mysql_version,
        'tls': tls_status,
        'counts': counts,
        'orphan_files': orphans,
        'source_commit': rel[0],
        'loaded_at': str(rel[3]),
        'sample_md5_lookup': list(sample_md5) if sample_md5 else None,
        'largest_files': largest_files,
    }


def verify():
    out = stack_outputs()
    release = json.loads((OUT / 's3-release.json').read_text())
    manifest = json.loads((OUT / 's3_downloaded' / 'manifest.json').read_text())
    secret = get_db_credentials(out['AdminSecretArn'])
    rds = aws_session().client('rds')
    db_info = rds.describe_db_instances(DBInstanceIdentifier=out['DBIdentifier'])['DBInstances'][0]

    conn = connect_mysql(out['DBEndpoint'], out['DBName'], secret['username'], secret['password'])
    try:
        result = verify_connection(conn, manifest, release['source_commit'])
    finally:
        conn.close()

    result.update({
        'db_identifier': out['DBIdentifier'],
        'multi_az': db_info['MultiAZ'],
        'primary_az': db_info['AvailabilityZone'],
        'secondary_az': db_info.get('SecondaryAvailabilityZone'),
        'storage_encrypted': db_info['StorageEncrypted'],
        'publicly_accessible': db_info['PubliclyAccessible'],
        's3_release': release,
        'status': 'PASS',
    })
    save_artifact('verification.json', result)
    print(json.dumps({
        'status': 'PASS',
        'database': result['database'],
        'mysql_version': result['mysql_version'],
        'tls': result['tls'],
        'multi_az': result['multi_az'],
        'primary_az': result['primary_az'],
        'secondary_az': result['secondary_az'],
        'counts': result['counts'],
    }, indent=2))


def failover_dr():
    """Verify Multi-AZ standby failover and manual snapshot backup/restore DR."""
    s = aws_session()
    rds = s.client('rds')
    s3 = s.client('s3')
    out = stack_outputs()
    release = json.loads((OUT / 's3-release.json').read_text())
    manifest = json.loads((OUT / 's3_downloaded' / 'manifest.json').read_text())
    secret = get_db_credentials(out['AdminSecretArn'])
    db_id = out['DBIdentifier']

    report = {'db_identifier': db_id, 'source_commit': release['source_commit']}

    # 1. Multi-AZ Standby Failover Test
    before = rds.describe_db_instances(DBInstanceIdentifier=db_id)['DBInstances'][0]
    report['multi_az_enabled'] = before['MultiAZ']
    report['primary_az_before'] = before['AvailabilityZone']
    report['secondary_az_before'] = before.get('SecondaryAvailabilityZone')

    if before['MultiAZ']:
        print(f"Initiating Multi-AZ forced failover on {db_id} (primary={before['AvailabilityZone']}, standby={before.get('SecondaryAvailabilityZone')})...")
        t0 = time.time()
        rds.reboot_db_instance(DBInstanceIdentifier=db_id, ForceFailover=True)
        # Wait for failover to begin and then reach available with swapped AZ
        deadline = time.time() + 900
        after = before
        while time.time() < deadline:
            time.sleep(15)
            after = rds.describe_db_instances(DBInstanceIdentifier=db_id)['DBInstances'][0]
            if after['DBInstanceStatus'] == 'available' and after['AvailabilityZone'] != before['AvailabilityZone']:
                break
        elapsed = round(time.time() - t0, 1)
        if after['AvailabilityZone'] == before['AvailabilityZone']:
            raise RuntimeError('Multi-AZ failover completed without switching Availability Zone')

        # Reconnect over TLS and verify zero data loss
        conn = connect_mysql(out['DBEndpoint'], out['DBName'], secret['username'], secret['password'])
        try:
            post_failover = verify_connection(conn, manifest, release['source_commit'])
        finally:
            conn.close()

        report['failover_test'] = {
            'status': 'PASS',
            'elapsed_seconds': elapsed,
            'primary_az_after': after['AvailabilityZone'],
            'secondary_az_after': after.get('SecondaryAvailabilityZone'),
            'tls_after_failover': post_failover['tls'],
            'counts_after_failover': post_failover['counts'],
        }
        print(f"Multi-AZ failover PASS in {elapsed}s: primary moved {before['AvailabilityZone']} -> {after['AvailabilityZone']}.")
    else:
        report['failover_test'] = {'status': 'SKIPPED', 'reason': 'MultiAZ is false on this instance'}

    # 2. Disaster Recovery: Manual Snapshot Backup & Restore Test
    stamp = time.strftime('%Y%m%d%H%M%S', time.gmtime())
    snap_id = f'nsrl-dr-snap-{stamp}'
    dr_db_id = f'nsrl-dr-restore-{stamp}'
    print(f'Creating manual RDS snapshot {snap_id}...')
    t_snap = time.time()
    rds.create_db_snapshot(DBInstanceIdentifier=db_id, DBSnapshotIdentifier=snap_id)
    rds.get_waiter('db_snapshot_available').wait(DBSnapshotIdentifier=snap_id)
    snap_elapsed = round(time.time() - t_snap, 1)
    print(f'Snapshot {snap_id} available in {snap_elapsed}s. Restoring DR instance {dr_db_id}...')

    t_res = time.time()
    rds.restore_db_instance_from_db_snapshot(
        DBInstanceIdentifier=dr_db_id,
        DBSnapshotIdentifier=snap_id,
        DBInstanceClass=before['DBInstanceClass'],
        DBSubnetGroupName=before['DBSubnetGroup']['DBSubnetGroupName'],
        VpcSecurityGroupIds=[sg['VpcSecurityGroupId'] for sg in before['VpcSecurityGroups']],
        DBParameterGroupName=before['DBParameterGroups'][0]['DBParameterGroupName'],
        MultiAZ=False,
        PubliclyAccessible=False,
        StorageType='gp2',
    )
    try:
        rds.get_waiter('db_instance_available').wait(DBInstanceIdentifier=dr_db_id)
        dr_elapsed = round(time.time() - t_res, 1)
        dr_info = rds.describe_db_instances(DBInstanceIdentifier=dr_db_id)['DBInstances'][0]
        dr_endpoint = dr_info['Endpoint']['Address']

        conn = connect_mysql(dr_endpoint, out['DBName'], secret['username'], secret['password'])
        try:
            dr_verify = verify_connection(conn, manifest, release['source_commit'])
        finally:
            conn.close()

        report['snapshot_dr_test'] = {
            'status': 'PASS',
            'snapshot_id': snap_id,
            'snapshot_seconds': snap_elapsed,
            'restored_db_identifier': dr_db_id,
            'restore_seconds': dr_elapsed,
            'restored_az': dr_info['AvailabilityZone'],
            'tls_on_restored_db': dr_verify['tls'],
            'counts_on_restored_db': dr_verify['counts'],
        }
        print(f'Snapshot DR restore PASS in {dr_elapsed}s on {dr_db_id}. Cleaning up temporary DR instance...')
    finally:
        report.setdefault('snapshot_dr_test', {'status': 'FAILED', 'snapshot_id': snap_id})
        try:
            rds.delete_db_instance(DBInstanceIdentifier=dr_db_id, SkipFinalSnapshot=True, DeleteAutomatedBackups=True)
            report['snapshot_dr_test']['temporary_dr_instance_cleanup'] = 'delete_requested'
        except Exception as exc:
            report['snapshot_dr_test']['temporary_dr_instance_cleanup'] = f'cleanup_error: {exc}'

    report['status'] = 'PASS'
    save_artifact('dr-verification.json', report)
    key = f"reports/{release['source_commit']}/{os.environ.get('BUILD_NUMBER', 'manual')}/dr-verification.json"
    s3.put_object(
        Bucket=out['ScriptsBucketName'],
        Key=key,
        Body=(OUT / 'dr-verification.json').read_bytes(),
        ServerSideEncryption='AES256',
        ContentType='application/json',
    )
    print(f"Saved DR & failover verification report privately to s3://{out['ScriptsBucketName']}/{key}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['preflight', 'deploy', 'publish', 'download', 'load', 'verify', 'failover_dr'])
    args = parser.parse_args()
    try:
        globals()[args.stage]()
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
