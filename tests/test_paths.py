from claims.telemetry import RUNTIME_ROOT, outcomes_path
from evals import paths


def test_generated_files_share_one_gitignored_root():
    assert RUNTIME_ROOT.name == ".runtime"
    for name in paths.__all__:
        assert RUNTIME_ROOT in getattr(paths, name).parents or getattr(paths, name) == RUNTIME_ROOT
    assert outcomes_path() == paths.OUTCOMES
