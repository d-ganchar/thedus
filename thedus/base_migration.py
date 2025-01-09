from ripley import ClickhouseProtocol


class BaseMigration:
    def __init__(self, clickhouse: ClickhouseProtocol):
        self._clickhouse: ClickhouseProtocol = clickhouse

    @classmethod
    def get_env_to_skip(cls) -> list:
        return []

    def up(self):
        pass

    def down(self):
        pass
