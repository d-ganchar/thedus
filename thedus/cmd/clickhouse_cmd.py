import codecs
import dataclasses
import sys
import logging
from datetime import datetime
from typing import List

from ripley import ClickhouseProtocol
from rich.console import Console
from rich.table import Table
from rich import box

from .abstract_cmd import AbstractCmd


@dataclasses.dataclass
class _MigrationFile:
    migration_path: str
    revision: str


@dataclasses.dataclass
class _AppliedMigration:
    command: str
    environment: str
    revision: str
    is_skipped: bool
    created_at: datetime


def _parse_migration_path(migration_path: str) -> _MigrationFile:
    path_items = migration_path.split('/')
    migration_file = path_items.pop()
    revision = migration_file.replace('.py', '')
    return _MigrationFile(migration_path, revision)


def _get_applied_migrations(clickhouse: ClickhouseProtocol) -> List[_AppliedMigration]:
    migrations = clickhouse.exec("""
        SELECT command, revision, is_skipped, environment, created_at
          FROM thedus_migration_log
         ORDER BY version DESC
         LIMIT 20
    """)

    return [
        _AppliedMigration(command=m[0], revision=m[1], is_skipped=bool(m[2]), environment=m[3], created_at=m[4])
        for m in migrations
    ]


def _get_applied_migrations_before_revision(clickhouse: ClickhouseProtocol, revision: str) -> List:
    migrations = clickhouse.exec("""
        SELECT command, revision, is_skipped, environment, created_at
          FROM thedus_migration_log
         WHERE version <= (
            SELECT version
              FROM thedus_migration_log
             WHERE startsWith(revision, %(revision)s)
             ORDER BY version DESC
             LIMIT 1
         )
         ORDER BY version DESC
         LIMIT 20
    """, params=dict(revision=revision))

    return [
        _AppliedMigration(command=m[0], revision=m[1], is_skipped=bool(m[2]), environment=m[3], created_at=m[4])
        for m in migrations
    ]


class _AbstractChangeDbStateCmd(AbstractCmd):
    def __init__(
        self,
        clickhouse: ClickhouseProtocol,
        thedus_dir: str,
        thedus_env: str,
        to_revision: str = '',
    ):
        sys.path.append(thedus_dir)

        self._clickhouse = clickhouse
        self._migration_files: List[str] | None = None
        self._to_revision = to_revision
        self._thedus_env = thedus_env

    @property
    def command(self) -> str:
        raise NotImplementedError()

    def set_migration_files(self, migration_files: List[str]) -> List[str]:
        """
        Set list of all migration files by priority to execute
        """
        if not self._to_revision:
            self._migration_files = migration_files
            return self._migration_files

        for migration in migration_files:
            path_items = migration.split('/')
            migration_file = path_items.pop()
            revision = migration_file.replace('.py', '')

            if revision == self._to_revision:
                self._migration_files = migration_files
                return self._migration_files

    def _parse_migration_path(self, migration_path: str) -> _MigrationFile:
        path_items = migration_path.split('/')
        migration_file = path_items.pop()
        revision = migration_file.replace('.py', '')
        return _MigrationFile(migration_path, revision)

    def _write_migration_log(self, revision: str, is_skipped: bool = False) -> None:
        self._clickhouse.exec("""
            INSERT INTO thedus_migration_log (command, revision, environment, version, is_skipped, created_at)
            SELECT %(command)s, %(revision)s, %(environment)s, max(version) + 1, %(is_skipped)s, now()
              FROM thedus_migration_log
             LIMIT 1
        """, params=dict(
                command=self.command,
                revision=revision,
                environment=self._thedus_env,
                is_skipped=int(is_skipped),
            ),
        )

    def _exec_file(self, migration_file: _MigrationFile, revision: str = None) -> None:
        with codecs.open(migration_file.migration_path, 'r', encoding='utf-8') as file:
            exec(file.read())

        log_revision = revision if revision is not None else migration_file.revision
        skip_migration = eval('self._thedus_env in Migration.get_env_to_skip()')
        if skip_migration:
            logging.warning(f'SKIP {migration_file.revision}')
            self._write_migration_log(log_revision, True)
            return

        logging.info(self._get_before_exec_msg(migration_file))
        exec(f'Migration(self._clickhouse).{self.migration_method}')
        self._write_migration_log(log_revision)

    @property
    def migration_method(self):
        raise NotImplementedError()

    def _get_before_exec_msg(self, migration_file: _MigrationFile) -> str:
        raise NotImplementedError()


class StateCmd(AbstractCmd):
    def __init__(self, clickhouse: ClickhouseProtocol, migrations: List[str],  before_revision: str = ''):
        self._clickhouse = clickhouse
        self._before_revision = before_revision
        self._migrations = migrations

    @property
    def table_header_style(self) -> str:
        return 'deep_sky_blue1'

    def _create_table(self, revision_style: str = '') -> Table:
        table = Table(
            title='Migrations',
            header_style=self.table_header_style,
            title_style=self.table_header_style,
            border_style=self.table_header_style,
            box=box.MINIMAL_DOUBLE_HEAD,
        )

        table.add_column('Command')
        table.add_column('Environment')
        table.add_column('Revision', style=revision_style)
        table.add_column('Run date')
        return table

    def _get_footer_stat(self):
        return self._clickhouse.exec("""
            SELECT sum(is_skipped) AS skipped,
                   count(DISTINCT environment) AS environments,
                   max(version) AS runs,
                   min(created_at) AS min_created_at
              FROM thedus_migration_log
        """)[0]

    def _show_applied_migrations(self, applied: List[_AppliedMigration]):
        applied.reverse()
        table = self._create_table()

        for ix, migration in enumerate(applied):
            if migration.command.startswith('upgrade'):
                style = 'green'
                if migration.is_skipped:
                    style = 'dark_green'
            else:
                style = 'red'
                next_raw = ix + 1
                if (
                    next_raw < len(applied) and
                    applied[next_raw].command == migration.command and
                    applied[next_raw].is_skipped
                ):
                    style = 'dark_red'

            table.add_row(
                migration.command,
                migration.environment,
                migration.revision,
                str(migration.created_at),
                style=style,
            )

        print_file = False
        for migration_path in self._migrations:
            migration_file = _parse_migration_path(migration_path)
            if migration_file.revision == applied[-1].revision:
                print_file = True
                continue

            if print_file:
                table.add_row('', '', migration_file.revision, '', style='bright_black')

        def _to_footer_stat(counter: int, label: str) -> str:
            return f'[{self.table_header_style}]{counter}[/{self.table_header_style}] {label}'

        skipped, environments, runs, min_dt = self._get_footer_stat()
        console = Console()
        console.print(table)
        console.print(', '.join([
            _to_footer_stat(len(self._migrations), 'files'),
            _to_footer_stat(runs, 'runs'),
            _to_footer_stat(skipped, 'skipped'),
            _to_footer_stat(environments, 'environments'),
            _to_footer_stat(min_dt, 'first migration'),
        ]))

    def run(self):
        if self._before_revision:
            applied = _get_applied_migrations_before_revision(self._clickhouse, self._before_revision)
        else:
            applied = _get_applied_migrations(self._clickhouse)

        if applied:
            self._show_applied_migrations(applied)
            return

        table = self._create_table('bright_black')
        for migration_path in self._migrations:
            table.add_row('', '', _parse_migration_path(migration_path).revision, '')

        console = Console()
        console.print(table)


class DowngradeCmd(_AbstractChangeDbStateCmd):
    @property
    def migration_method(self):
        return 'down()'

    @property
    def command(self) -> str:
        return 'downgrade' + (f' {self._to_revision}' if self._to_revision else '')

    def _get_before_exec_msg(self, migration_file: _MigrationFile) -> str:
        return f'rollback {migration_file.revision}'

    def run(self):
        applied = _get_applied_migrations(self._clickhouse)
        if not applied:
            logging.warning('no applied migrations found')
            return

        last_revision = applied[0].revision
        if last_revision == '':
            return

        run_migration = False
        for ix, migration in enumerate(self._migration_files):
            migration_file = _parse_migration_path(migration)
            if last_revision == migration_file.revision:
                run_migration = True

            if run_migration:
                try:
                    next_file = _parse_migration_path(self._migration_files[ix + 1])
                    next_revision = next_file.revision
                except IndexError:
                    # first migration rollback
                    next_revision = ''

                self._exec_file(migration_file, next_revision)
                if not self._to_revision or migration_file.revision == self._to_revision:
                    return


class UpgradeCmd(_AbstractChangeDbStateCmd):
    def _get_before_exec_msg(self, migration_file: _MigrationFile) -> str:
        return f'upgrade to {migration_file.revision}'

    @property
    def migration_method(self):
        return 'up()'

    @property
    def command(self) -> str:
        return 'upgrade' + (f' {self._to_revision}' if self._to_revision else '')

    def run(self):
        applied = _get_applied_migrations(self._clickhouse)
        last_revision = ''

        if applied:
            last_revision = applied[0].revision
            run_migration = True if last_revision == '' else False
        else:
            run_migration = True

        for migration in self._migration_files:
            migration_file = _parse_migration_path(migration)

            if run_migration:
                self._exec_file(migration_file)
            if last_revision == migration_file.revision:
                run_migration = True
            if self._to_revision == migration_file.revision:
                break
