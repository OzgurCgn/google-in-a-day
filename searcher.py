from indexer import IndexerManager


class Searcher:
    def __init__(self, manager: IndexerManager):
        self.manager = manager

    def search(self, query, limit=50):
        return self.manager.search(query=query, limit=limit)