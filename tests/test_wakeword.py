import subprocess
import sys


def test_loading_openwakeword_skips_sklearn_and_scipy_stats():
    """openwakeword's __init__ drags in sklearn (~1.5 s) for a training helper Jarvis never uses."""
    code = ("import sys, jarvis.wakeword as w; w._openwakeword(); "
            "print(any(m.startswith('sklearn') for m in sys.modules), 'openwakeword.model' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["False", "True"]


def test_custom_verifier_training_explains_itself_instead_of_crashing_on_import():
    import pytest
    from jarvis import wakeword
    wakeword._openwakeword()
    import openwakeword
    with pytest.raises(RuntimeError, match="not available"):
        openwakeword.train_custom_verifier()
