from waku import Module


def test_iter_submodules() -> None:
    mod1 = Module('mod1')
    mod2 = Module('mod2', imports=[mod1])
    mod3 = Module('mod3', imports=[mod1, mod2])
    assert list(mod3.iter_submodules()) == [mod3, mod1, mod2]
