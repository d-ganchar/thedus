import abc


class AbstractCmd(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def run(self):
        pass
