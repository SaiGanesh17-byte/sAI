from repository.context import RepositoryContext


class _Scanner:
    def __init__(self, files):
        self.files = files

    def scan(self):
        return self.files


class _Cache:
    def get_file_cache(self, rel):
        return {"symbols": {"classes": [{"name": "Big", "methods": [f"m{i}" for i in range(20)]}], "functions": []}}


def _ctx(tmp_path, n_files):
    files = [tmp_path / f"mod_{i:03d}.py" for i in range(n_files)]
    ctx = RepositoryContext.__new__(RepositoryContext)
    ctx.workspace_path = tmp_path
    ctx.scanner = _Scanner(files)
    ctx.cache = _Cache()
    return ctx


def test_small_map_is_returned_in_full(tmp_path):
    ctx = _ctx(tmp_path, 2)
    out = ctx.get_repo_map(max_chars=10_000)
    assert "def m19()" in out and "truncated" not in out


def test_map_drops_symbols_before_files(tmp_path):
    ctx = _ctx(tmp_path, 20)
    full = ctx.get_repo_map()
    out = ctx.get_repo_map(max_chars=len(full) // 3)
    assert len(out) <= len(full) // 3
    assert "mod_019.py" in out and "def m0()" not in out


def test_map_never_exceeds_budget(tmp_path):
    ctx = _ctx(tmp_path, 500)
    out = ctx.get_repo_map(max_chars=2000)
    assert len(out) <= 2000
    assert "codebase_search" in out  # tells the agent how to get the rest
