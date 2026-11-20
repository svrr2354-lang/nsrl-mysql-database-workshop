import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'app'))

import nsrl_gui
import workshop


class WorkshopTests(unittest.TestCase):
    def _valid_params(self):
        return {
            'DB_NAME': 'jj_nsrl',
            'STORAGE_GB': '20',
            'DB_INSTANCE_CLASS': 'db.t3.micro',
            'VPC_ID': 'vpc-0123456789abcdef0',
            'DB_SUBNET_GROUP': 'nsrl-mysql-dbsubnetgroup-abc',
            'JENKINS_SECURITY_GROUP_ID': 'sg-0123456789abcdef0',
            'STACK_NAME': 'nsrl-mysql-db',
        }

    def test_valid_parameters_pass(self):
        workshop.validate(self._valid_params())

    def test_rejects_invalid_db_name(self):
        p = self._valid_params()
        p['DB_NAME'] = '123-bad-db'
        with self.assertRaises(ValueError):
            workshop.validate(p)

    def test_rejects_storage_out_of_range(self):
        p = self._valid_params()
        p['STORAGE_GB'] = '10'
        with self.assertRaises(ValueError):
            workshop.validate(p)

    def test_manifest_and_data_files_exist(self):
        manifest = json.loads((ROOT / 'data' / 'manifest.json').read_text())
        self.assertEqual(manifest['tables']['file']['rows'], 10000)
        self.assertEqual(manifest['tables']['pkg']['rows'], 85885)
        self.assertEqual(manifest['tables']['os']['rows'], 309)
        self.assertEqual(manifest['tables']['mfg']['rows'], 2624)
        for fname in workshop.NSRL_FILES:
            self.assertTrue((ROOT / 'data' / fname).exists(), fname)

    def test_schema_enforces_mysql_innodb_and_indexes(self):
        schema = (ROOT / 'sql' / 'nsrl_mysql_schema.sql').read_text()
        self.assertIn('CREATE TABLE nsrl_file', schema)
        self.assertIn('ENGINE=InnoDB', schema)
        self.assertIn('CREATE OR REPLACE VIEW nsrl_file_details', schema)
        self.assertIn('idx_nsrl_file_md5', schema)

    def test_search_query_builder_exact_md5_and_size(self):
        sql, args, count_sql, count_args = nsrl_gui.build_search_query({
            'hash': '51580c2aea9de8e933c858a34502f78d',
            'min_size': '1000',
            'limit': '10',
        })
        self.assertIn('md5 = %s', sql)
        self.assertIn('file_size >= %s', sql)
        self.assertEqual(args, ['51580C2AEA9DE8E933C858A34502F78D', 1000, 10])
        self.assertEqual(count_args, ['51580C2AEA9DE8E933C858A34502F78D', 1000])
        self.assertIn('COUNT(*)', count_sql)


if __name__ == '__main__':
    unittest.main()
