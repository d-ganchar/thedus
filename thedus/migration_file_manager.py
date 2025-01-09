import codecs
import os
import re
from datetime import datetime, timezone
from typing import List

from .env_manager import EnvManager as Env


class MigrationFileManager:
    @staticmethod
    def create_migration(name: str) -> str:
        name = '_'.join([datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S'), name])
        migration_path = os.path.join(Env.get_thedus_dir(), name) + '.py'

        with codecs.open(migration_path, 'w', encoding='utf-8') as file:
            file.write("""from thedus.base_migration import BaseMigration


class Migration(BaseMigration):
    def up(self):
        self._clickhouse.exec('SELECT 1')
        
    def down(self):
        self._clickhouse.exec('SELECT 1')
""")
            return migration_path

    @staticmethod
    def get_migrations(asc: bool = True) -> List[str]:
        """
        Returns a list of all migrations in the thedus directory
        """
        files = []
        for filename in os.listdir(Env.get_thedus_dir()):
            if not re.fullmatch('^[0-9]{14}[a-z_]+.py$', filename):
                continue

            files.append(os.path.join(Env.get_thedus_dir(), filename))

        files = sorted(files)
        if asc:
            return files

        files.reverse()
        return files
