import codecs
import logging
import os
import re
from logging.config import dictConfig

import typer
from clickhouse_driver import Client
from clickhouse_driver.errors import Error
from ripley import ClickhouseProtocol, from_clickhouse
from typing_extensions import Annotated

from .cmd.clickhouse_cmd import StateCmd, UpgradeCmd, DowngradeCmd
from .env_manager import EnvManager as Env
from .migration_file_manager import MigrationFileManager as Migrations

dictConfig({
    'version': 1,
    'disable_existing_loggers': True,
    'formatters': {
        'standard': {
            'format': '%(asctime)s [%(levelname)s]: %(message)s'
        },
    },
    'handlers': {
        'default': {
            'level': 'INFO',
            'formatter': 'standard',
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stdout',
        },
    },
    'loggers': {
        '': {
            'handlers': ['default'],
            'level': 'INFO',
            'propagate': True,
        },
    }
})


def init_clickhouse(create_migration_log: bool=True) -> ClickhouseProtocol:
    host = Env.get_clickhouse_host()
    db = Env.get_clickhouse_db()

    try:
        clickhouse = from_clickhouse(Client(
            host=host,
            port=Env.get_clickhouse_port(),
            user=Env.get_clickhouse_user(),
            password=Env.get_clickhouse_password(),
            database=db,
        ))

        if create_migration_log:
            clickhouse.exec("""
            CREATE TABLE IF NOT EXISTS thedus_migration_log
            (
                command String,
                revision String,
                environment String,
                version UInt64,
                is_skipped UInt8 default 0,
                created_at Datetime default now()
            )
            ENGINE = Log
            """)

        return clickhouse
    except (Error, ConnectionRefusedError) as error:
        logging.error(error)
        exit(1)


app = typer.Typer(
    help=f'host {Env.get_clickhouse_host()}, db: {Env.get_clickhouse_db()}, THEDUS_DIR: {Env.get_thedus_dir()}',
    add_completion=False,
)


@app.command(help='Show migrations state')
def state(
    before_revision: Annotated[
        str,
        typer.Argument(metavar='TEXT', help='Shows completed migrations before a revision. The value can only '
                                            'use the beginning of a revision name, example: 20250102')
    ] = '',
):
    StateCmd(
        init_clickhouse(),
        Migrations.get_migrations(),
        before_revision,
    ).run()


@app.command(help='Generates a new migration file. example: thedus create-migration create_metrics')
def create_migration(name: str):
    pattern = '[a-z_]{6,30}'
    if re.fullmatch(pattern, name):
        migration_path = Migrations.create_migration(name)
        logging.info(f'{migration_path} created')
        return

    logging.info(f'Invalid migration name "{name}". The name must match reg exp {pattern}')
    exit(1)


@app.command(help='Apply migrations. All by default')
def upgrade(
    to_revision: Annotated[
        str,
        typer.Argument(
            metavar='TEXT',
            help='Upgrade to specific revision. example: thedus upgrade 20250101000000_create_metrics'
        ),
    ] = '',
):
    found = Migrations.get_migrations()
    if not found:
        logging.warning('Migration files not found')
        return

    upgrade_cmd = UpgradeCmd(
        clickhouse=init_clickhouse(),
        thedus_dir=Env.get_thedus_dir(),
        thedus_env=Env.get_thedus_env(),
        to_revision=to_revision,
    )

    if upgrade_cmd.set_migration_files(found) is None:
        logging.error(f'revision {to_revision} not found')
        exit(1)

    upgrade_cmd.run()
    logging.info('done')


@app.command(help='Roll back migrations. The last one migration by default')
def downgrade(
    to_revision: Annotated[
        str,
        typer.Argument(
            metavar='TEXT',
            help='Roll back to specific revision. example: thedus downgrade 20250101000000_create_metrics'
        ),
    ] = '',
):
    found = Migrations.get_migrations(False)
    if not found:
        logging.warning('Migration files not found')
        return

    downgrade_cmd = DowngradeCmd(
        clickhouse=init_clickhouse(),
        thedus_dir=Env.get_thedus_dir(),
        thedus_env=Env.get_thedus_env(),
        to_revision=to_revision,
    )

    if downgrade_cmd.set_migration_files(found) is None:
        logging.error(f'revision {to_revision} not found')
        exit(1)

    downgrade_cmd.run()
    logging.info('done')


@app.command(help='Saves a database structure to a file, system.tables.create_table_query is used to generate the DDL')
def save_db_structure(
    db_name: Annotated[
        str,
        typer.Argument(
            metavar='TEXT',
            help='Database name'
        ),
    ] = Env.get_clickhouse_db(),
    file_path: Annotated[
        str,
        typer.Argument(
            metavar='TEXT',
            help='Full path to output file. default: "./$CLICKHOUSE_DB.sql"'
        ),
    ] = f'./{Env.get_clickhouse_db()}.sql',
):
    if os.path.exists(file_path):
        logging.error(f'File {file_path} already exists')
        exit(1)

    clickhouse = init_clickhouse(False)
    tables = clickhouse.get_tables_by_db(db_name)
    tables = sorted(
        tables,
        key=lambda t: (t.engine.lower() in ('view', 'materializedview'), t.metadata_modification_time)
    )

    if not tables:
        logging.warning('"%s" objects not found', db_name)
        exit(0)

    with codecs.open(file_path, 'w') as file:
        file.write(';\n'.join([t.create_table_query for t in tables]))

    logging.info('%s created', file_path)


if __name__ == '__main__':
    app()
