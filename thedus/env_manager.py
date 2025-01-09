import os
from pathlib import Path


class EnvManager:
    @staticmethod
    def get_clickhouse_host() -> str:
        return os.environ.get('CLICKHOUSE_HOST') or 'localhost'

    @staticmethod
    def get_clickhouse_port() -> int:
        return os.environ.get('CLICKHOUSE_PORT') or 9000

    @staticmethod
    def get_clickhouse_user() -> str:
        return os.environ.get('CLICKHOUSE_USER') or 'default'

    @staticmethod
    def get_clickhouse_db() -> str:
        return os.environ.get('CLICKHOUSE_DB') or 'default'

    @staticmethod
    def get_clickhouse_password() -> str:
        return os.environ.get('CLICKHOUSE_PASSWORD') or ''

    @staticmethod
    def get_thedus_env() -> str:
        """
        THEDUS environment name. used to allow skipping migration
        see: BaseMigration.get_env_to_skip() + upgrade / downgrade
        """
        return os.environ.get('THEDUS_ENV') or 'dev'

    @staticmethod
    def get_thedus_dir() -> str:
        """
        Returns full path to migrations folder
        """
        return os.environ.get('THEDUS_DIR') or str(Path.cwd())
