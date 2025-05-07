import codecs
import os
import subprocess
import unittest
from typing import List

from clickhouse_driver import Client
from ripley import from_clickhouse
from parameterized import parameterized


_CLICKHOUSE_DB = 'thedus_tests'
_THEDUS_DIR = os.path.join(os.path.dirname(__file__), 'migrations')

os.environ['THEDUS_DIR'] = _THEDUS_DIR
os.environ['CLICKHOUSE_DB'] = _CLICKHOUSE_DB
os.environ['CLICKHOUSE_PASSWORD'] = _CLICKHOUSE_DB
os.environ['CLICKHOUSE_USER'] = _CLICKHOUSE_DB


class BaseCliTest(unittest.TestCase):
    maxDiff = 10000
    clickhouse = Client(
        host='localhost',
        port=9000,
        user=_CLICKHOUSE_DB,
        password=_CLICKHOUSE_DB,
        database=_CLICKHOUSE_DB,
    )

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
        os.environ['THEDUS_ENV'] = ''

        with os.scandir(_THEDUS_DIR) as entries:
            for entry in entries:
                if entry.name == '.gitignore':
                    continue
                os.remove(entry.path)

        clickhouse = from_clickhouse(self.clickhouse)
        for table in clickhouse.get_tables_by_db(_CLICKHOUSE_DB):
            self.clickhouse.execute(f"DROP TABLE {table.full_name}")

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
                 ORDER BY version
            """),
        )

    def check_thedus_output(self, output: str, expected: List[str]):
        output = output.split('\n')
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
        result = subprocess.getoutput('thedus upgrade')
        self.check_thedus_output(result, expected_output)
        expected = [
            self.clickhouse.execute(f'SELECT count() FROM {t}')
            for t in self.test_tables
        ]

        self.assertEqual(expected, expected_table_records)
        self.check_thedus_migration_log(expected_thedus_logs)

        # 1 downgrade
        result = subprocess.getoutput('thedus downgrade')
        self.check_thedus_output(result, ['rollback 20250101000003_create_tbl_logs', 'done'])
        self.assertEqual(
            [],
            self.clickhouse.execute(
                f"SELECT * FROM system.tables WHERE table = 'logs' AND database = '{_CLICKHOUSE_DB}'"
            ))

    def test_upgrade_to_revision(self):
        result = subprocess.getoutput('thedus upgrade 20250101000000_create_tbl_metrics')
        self.check_thedus_output(result, ['upgrade to 20250101000000_create_tbl_metrics', 'done'])
        self.check_thedus_migration_log([
            ('upgrade 20250101000000_create_tbl_metrics', '20250101000000_create_tbl_metrics', 'dev', 0),
        ])

        self.assertEqual([(0,)], self.clickhouse.execute('SELECT count() FROM metrics'))
        result = subprocess.getoutput('thedus upgrade 20250101000002_create_tbl_events')
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

        self.assertEqual([(0,)], self.clickhouse.execute('SELECT count() FROM events'))

    def test_downgrade_to_revision(self):
        subprocess.getoutput('thedus upgrade')
        result = subprocess.getoutput('thedus downgrade 20250101000001_insert_into_metrics')

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
                 WHERE table IN ('logs', 'events') AND database = '{_CLICKHOUSE_DB}'""")
        )

        self.check_thedus_migration_log([
            ('upgrade', '20250101000000_create_tbl_metrics', 'dev', 0),
            ('upgrade', '20250101000001_insert_into_metrics', 'dev', 1),
            ('upgrade', '20250101000002_create_tbl_events', 'dev', 0),
            ('upgrade', '20250101000003_create_tbl_logs', 'dev', 0),
            ('downgrade 20250101000001_insert_into_metrics', '20250101000002_create_tbl_events', 'dev', 0),
            ('downgrade 20250101000001_insert_into_metrics', '20250101000001_insert_into_metrics', 'dev', 0),
            ('downgrade 20250101000001_insert_into_metrics', '20250101000000_create_tbl_metrics', 'dev', 1)])

        result = subprocess.getoutput('thedus downgrade')
        self.check_thedus_output(result, ['rollback 20250101000000_create_tbl_metrics', 'done'])
        result = subprocess.getoutput('thedus downgrade')
        self.check_thedus_output(result, ['done'])


class TestSaveDbStructure(BaseCliTest):
    def test_save_db_structure(self):
        self.clickhouse.execute("""
            CREATE TABLE votes
            (
                `Id` UInt32,
                `PostId` Int32,
                `VoteTypeId` UInt8,
                `CreationDate` DateTime64(3, 'UTC'),
                `UserId` Int32,
                `BountyAmount` UInt8
            )
            ENGINE = MergeTree
            ORDER BY (VoteTypeId, CreationDate, PostId)
        """)

        self.clickhouse.execute("""
            CREATE TABLE up_down_votes_per_day
            (
              `Day` Date,
              `UpVotes` UInt32,
              `DownVotes` UInt32
            )
            ENGINE = SummingMergeTree
            ORDER BY Day
        """)

        self.clickhouse.execute("""
            CREATE MATERIALIZED VIEW up_down_votes_per_day_mv TO up_down_votes_per_day AS
            SELECT toStartOfDay(CreationDate)::Date AS Day,
                   countIf(VoteTypeId = 2) AS UpVotes,
                   countIf(VoteTypeId = 3) AS DownVotes
              FROM votes
             GROUP BY Day
        """)

        self.clickhouse.execute("""
            CREATE VIEW upvotes_per_user AS
            SELECT toDate(CreationDate) AS Day,
                   UserId,
                   count() AS user_votes
              FROM votes
             GROUP BY Day, UserId
        """)

        result = subprocess.run(
            'thedus save-db-structure',
            shell=True,
            cwd=self.thedus_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=os.environ,
        )

        filename = f'{_CLICKHOUSE_DB}.sql'
        self.assertEqual(result.stdout[32:], f'./{filename} created\n')

        with codecs.open(os.path.join(self.thedus_dir, filename)) as file:
            self.assertEqual(
                (
                    f"CREATE TABLE {_CLICKHOUSE_DB}.up_down_votes_per_day "
                    "(`Day` Date, `UpVotes` UInt32, `DownVotes` UInt32) "
                    "ENGINE = SummingMergeTree ORDER BY Day SETTINGS index_granularity = 8192;\n"
                    f"CREATE TABLE {_CLICKHOUSE_DB}.votes (`Id` UInt32, `PostId` Int32, `VoteTypeId` UInt8, "
                    "`CreationDate` DateTime64(3, 'UTC'), `UserId` Int32, `BountyAmount` UInt8) ENGINE = MergeTree "
                    "ORDER BY (VoteTypeId, CreationDate, PostId) SETTINGS index_granularity = 8192;\n"
                    "CREATE MATERIALIZED VIEW "
                    f"{_CLICKHOUSE_DB}.up_down_votes_per_day_mv TO {_CLICKHOUSE_DB}.up_down_votes_per_day "
                    "(`Day` Date, `UpVotes` UInt64, `DownVotes` UInt64) AS "
                    "SELECT CAST(toStartOfDay(CreationDate), 'Date') AS Day, countIf(VoteTypeId = 2) AS UpVotes, "
                    f"countIf(VoteTypeId = 3) AS DownVotes FROM {_CLICKHOUSE_DB}.votes GROUP BY Day;\n"
                    f"CREATE VIEW {_CLICKHOUSE_DB}.upvotes_per_user (`Day` Date, `UserId` Int32, `user_votes` UInt64) "
                    f"AS SELECT toDate(CreationDate) AS Day, UserId, count() AS user_votes FROM {_CLICKHOUSE_DB}.votes "
                    "GROUP BY Day, UserId"
                ),
                file.read(),
            )