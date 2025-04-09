import codecs
import os
import re
from typing import List

from .env_manager import EnvManager as Env


class MigrationFileManager:
    MIGRATION_FILENAME_PATTERN = '^[0-9]{14}[a-z0-9_]+.py$'

    @staticmethod
    def create_migration(file_path: str) -> str:
        with codecs.open(file_path, 'w', encoding='utf-8') as file:
            file.write("""from thedus.base_migration import BaseMigration


class Migration(BaseMigration):
    def up(self):
        self._clickhouse.exec('SELECT 1')
        
    def down(self):
        self._clickhouse.exec('SELECT 1')
""")
            return file_path

    @classmethod
    def get_migrations(cls, asc: bool = True) -> List[str]:
        """
        Returns a list of all migrations in the thedus directory
        """
        files = []
        for filename in os.listdir(Env.get_thedus_dir()):
            if cls.is_valid_migration_file(filename):
                files.append(os.path.join(Env.get_thedus_dir(), filename))

        files = sorted(files)
        if asc:
            return files

        files.reverse()
        return files

    @classmethod
    def is_valid_migration_file(cls, filename: str) -> bool:
        if re.fullmatch(cls.MIGRATION_FILENAME_PATTERN, filename):
            return True
