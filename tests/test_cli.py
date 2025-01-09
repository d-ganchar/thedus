import codecs
import os
import subprocess
import unittest

import platform
from typing import List

from parameterized import parameterized
from clickhouse_driver import Client


def init_clickhouse(database: str = 'default') -> Client:
    return Client(
        host='localhost',
        port=9000,
        user='default',
        password='',
        database=database,
    )


class BaseCliTest(unittest.TestCase):
    maxDiff = 10000
    clickhouse = init_clickhouse()

    @property
    def db_name(self):
        minor, major, _ = platform.python_version().split('.')
        return 'thedus_' + '_'.join([minor, major])

    @property
    def thedus_dir(self) -> str:
        return os.environ['THEDUS_DIR']

    @property
    def test_tables(self) -> List[str]:
        return [
            'metrics',
            'events',
            'logs',
        ]

    def setUp(self):
        thedus_dir = os.path.join(os.path.dirname(__file__), 'migrations')
        os.environ['THEDUS_ENV'] = ''
        os.environ['THEDUS_DIR'] = thedus_dir
        os.environ['CLICKHOUSE_DB'] = self.db_name

        with os.scandir(thedus_dir) as entries:
            for entry in entries:
                if entry.name == '.gitignore':
                    continue
                os.remove(entry.path)

        self.clickhouse = init_clickhouse()
        self.clickhouse.execute(f'DROP DATABASE IF EXISTS {self.db_name}')
        self.clickhouse.execute(f'CREATE DATABASE IF NOT EXISTS {self.db_name}')
        self.clickhouse = init_clickhouse(self.db_name)

        for file_name, up, down, skip_env in (
            (
                '0_create_tbl_metrics',
                'CREATE TABLE metrics (value UInt8) ENGINE = Log',
                'DROP TABLE IF EXISTS metrics',
                '',
            ),
            (
                '1_insert_into_metrics',
                'INSERT INTO metrics (value) VALUES (0)',
                'SELECT 1',
                'dev',
            ),
            (
                '2_create_tbl_events',
                'CREATE TABLE events (name String) ENGINE = Log',
                'DROP TABLE IF EXISTS events',
                '',
            ),
            (
                '3_create_tbl_logs',
                'CREATE TABLE logs (name String) ENGINE = Log',
                'DROP TABLE IF EXISTS logs',
                '',
            ),
        ):
            file_path = os.path.join(self.thedus_dir, f'2025010100000{file_name}.py')
            get_env_to_skip = f"""
    @classmethod
    def get_env_to_skip(cls) -> list:
        return ['{skip_env}']
            """ if skip_env else ''

            with codecs.open(file_path, mode='w', encoding='utf-8') as file:
                file.write(f"""from thedus.base_migration import BaseMigration
                

class Migration(BaseMigration):
    def up(self):
        self._clickhouse.exec('{up}')
        
    def down(self):
        self._clickhouse.exec('{down}')
        
{get_env_to_skip}
""")

    def check_thedus_migration_log(self, expected: list):
        self.assertEqual(
            expected,
            self.clickhouse.execute("""
                SELECT command, revision, environment, is_skipped
                  FROM thedus_migration_log
                 ORDER BY created_at
            """),
        )

    def check_thedus_output(self, output: bytes, expected: List[str]):
        output = output.decode().split('\n')
        output.pop()
        output_log = [' '.join(line.split(' ')[3:]) for line in output]
        self.assertEqual(output_log, expected)


class TestCreateMigration(BaseCliTest):
    def test_create_migration(self):
        result = subprocess.run(
            ['thedus', 'create-migration', 'create_table_events'],
            env=os.environ,
            check=True,
            stdout=subprocess.PIPE
        )

        migration_path = result.stdout.decode()
        _, _, _, migration_path, _ = migration_path.split(' ')
        with codecs.open(migration_path, 'r', 'utf-8') as migration_file:
            self.assertEqual(
                migration_file.read(),
                """from thedus.base_migration import BaseMigration


class Migration(BaseMigration):
    def up(self):
        self._clickhouse.exec('SELECT 1')
        
    def down(self):
        self._clickhouse.exec('SELECT 1')
""")

    @parameterized.expand([
        [
            'dev',
            [
                'upgrade to 20250101000000_create_tbl_metrics',
                'SKIP 20250101000001_insert_into_metrics',
                'upgrade to 20250101000002_create_tbl_events',
                'upgrade to 20250101000003_create_tbl_logs',
                'done',
            ],
            [[(0,)], [(0,)], [(0,)]],
            [('upgrade', '20250101000000_create_tbl_metrics', 'dev', 0),
             ('upgrade', '20250101000001_insert_into_metrics', 'dev', 1),
             ('upgrade', '20250101000002_create_tbl_events', 'dev', 0),
             ('upgrade', '20250101000003_create_tbl_logs', 'dev', 0)],
        ],
        [
            'prod',
            [
                'upgrade to 20250101000000_create_tbl_metrics',
                'upgrade to 20250101000001_insert_into_metrics',
                'upgrade to 20250101000002_create_tbl_events',
                'upgrade to 20250101000003_create_tbl_logs',
                'done',
            ],
            [[(1,)], [(0,)], [(0,)]],
            [('upgrade', '20250101000000_create_tbl_metrics', 'prod', 0),
             ('upgrade', '20250101000001_insert_into_metrics', 'prod', 0),
             ('upgrade', '20250101000002_create_tbl_events', 'prod', 0),
             ('upgrade', '20250101000003_create_tbl_logs', 'prod', 0)],
        ],
    ])
    def test_upgrade_downgrade(
        self,
        thedus_env: str,
        expected_output: list,
        expected_table_records: list,
        expected_thedus_logs: list,
    ):
        os.environ['THEDUS_ENV'] = thedus_env
        result = subprocess.check_output(
            ['thedus', 'upgrade'],
            env=os.environ,
            stderr=subprocess.PIPE,
        )

        self.check_thedus_output(result, expected_output)
        expected = [
            self.clickhouse.execute(f'SELECT count() FROM {t}')
            for t in self.test_tables
        ]

        self.assertEqual(expected, expected_table_records)
        self.check_thedus_migration_log(expected_thedus_logs)

        # 1 downgrade
        result = subprocess.check_output(
            ['thedus', 'downgrade'],
            env=os.environ,
            stderr=subprocess.PIPE,
        )

        self.check_thedus_output(result, ['rollback 20250101000003_create_tbl_logs', 'done'])
        self.assertEqual(
            [],
            self.clickhouse.execute(
                f"SELECT * FROM system.tables WHERE table = 'logs' AND database = '{self.db_name}'"
            ))

    def test_upgrade_to_revision(self):
        result = subprocess.check_output(
            ['thedus', 'upgrade', '20250101000000_create_tbl_metrics'],
            env=os.environ,
            stderr=subprocess.PIPE,
        )

        self.check_thedus_output(result, ['upgrade to 20250101000000_create_tbl_metrics', 'done'])
        self.check_thedus_migration_log([
            ('upgrade 20250101000000_create_tbl_metrics', '20250101000000_create_tbl_metrics', 'dev', 0),
        ])

        self.assertEqual(
            [(0,)],
            self.clickhouse.execute('SELECT count() FROM metrics')
        )

        result = subprocess.check_output(
            ['thedus', 'upgrade', '20250101000002_create_tbl_events'],
            env=os.environ,
            stderr=subprocess.PIPE,
        )

        self.check_thedus_output(
            result,
            [
                'SKIP 20250101000001_insert_into_metrics',
                'upgrade to 20250101000002_create_tbl_events',
                'done',
            ])

        self.check_thedus_migration_log([
            ('upgrade 20250101000000_create_tbl_metrics', '20250101000000_create_tbl_metrics', 'dev', 0),
            ('upgrade 20250101000002_create_tbl_events', '20250101000001_insert_into_metrics', 'dev', 1),
            ('upgrade 20250101000002_create_tbl_events', '20250101000002_create_tbl_events', 'dev', 0),
        ])

        self.assertEqual(
            [(0,)],
            self.clickhouse.execute('SELECT count() FROM events')
        )

    def test_downgrade_to_revision(self):
        subprocess.check_output(['thedus', 'upgrade'], env=os.environ)
        result = subprocess.check_output(
            ['thedus', 'downgrade', '20250101000001_insert_into_metrics'],
            env=os.environ,
            stderr=subprocess.PIPE,
        )

        self.check_thedus_output(
            result,
            [
                'rollback 20250101000003_create_tbl_logs',
                'rollback 20250101000002_create_tbl_events',
                'SKIP 20250101000001_insert_into_metrics',
                'done',
            ]
        )

        self.assertEqual(
            [],
            self.clickhouse.execute(f"""
                SELECT *
                  FROM system.tables
                 WHERE table IN ('logs', 'events') AND database = '{self.db_name}'""")
        )

        self.check_thedus_migration_log([
            ('upgrade', '20250101000000_create_tbl_metrics', 'dev', 0),
            ('upgrade', '20250101000001_insert_into_metrics', 'dev', 1),
            ('upgrade', '20250101000002_create_tbl_events', 'dev', 0),
            ('upgrade', '20250101000003_create_tbl_logs', 'dev', 0),
            ('downgrade 20250101000001_insert_into_metrics', '20250101000002_create_tbl_events', 'dev', 0),
            ('downgrade 20250101000001_insert_into_metrics', '20250101000001_insert_into_metrics', 'dev', 0),
            ('downgrade 20250101000001_insert_into_metrics', '20250101000000_create_tbl_metrics', 'dev', 1)])

        result = subprocess.check_output(
            ['thedus', 'downgrade'],
            env=os.environ,
            stderr=subprocess.PIPE,
        )

        self.check_thedus_output(result, ['rollback 20250101000000_create_tbl_metrics', 'done'])
        result = subprocess.check_output(
            ['thedus', 'downgrade'],
            env=os.environ,
            stderr=subprocess.PIPE,
        )

        self.check_thedus_output(result, ['done'])
