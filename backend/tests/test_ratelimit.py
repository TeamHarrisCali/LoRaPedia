from lorapedia.ratelimit import RateLimiter


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_one_per_minute():
    clock = Clock()
    rl = RateLimiter(clock)
    assert rl.check("!a", 1, 60) == 0
    clock.t += 10
    assert rl.check("!a", 1, 60) == 50  # 50s until the window frees up
    clock.t += 50
    assert rl.check("!a", 1, 60) == 0


def test_nodes_are_independent():
    rl = RateLimiter(Clock())
    assert rl.check("!a", 1, 60) == 0
    assert rl.check("!b", 1, 60) == 0
    assert rl.check("!a", 1, 60) > 0


def test_sliding_window_allows_n():
    clock = Clock()
    rl = RateLimiter(clock)
    assert [rl.check("!a", 3, 60) for _ in range(3)] == [0, 0, 0]
    assert rl.check("!a", 3, 60) == 60


def test_rejected_hits_do_not_extend_the_window():
    clock = Clock()
    rl = RateLimiter(clock)
    rl.check("!a", 1, 60)
    for _ in range(5):
        clock.t += 10
        rl.check("!a", 1, 60)
    clock.t += 10
    assert rl.check("!a", 1, 60) == 0  # 60s after the one accepted hit


def test_disabled_when_zero():
    rl = RateLimiter(Clock())
    assert all(rl.check("!a", 0, 60) == 0 for _ in range(10))


def test_reserve_lines_a_burst_up_one_window_apart_and_never_rejects():
    clock = Clock()
    rl = RateLimiter(clock)
    assert [rl.reserve("!a", 1, 60) for _ in range(4)] == [0, 60, 120, 180]
    assert rl.pending("!a") == 3
    clock.t += 60
    assert rl.pending("!a") == 2  # the first held question's turn has arrived
    # Turns are held at +120s and +180s (from the start), so a new one goes behind them at +240s: 180s from now.
    assert rl.reserve("!a", 1, 60) == 180


def test_reserve_with_a_larger_allowance():
    clock = Clock()
    rl = RateLimiter(clock)
    assert [rl.reserve("!a", 2, 60) for _ in range(5)] == [0, 0, 60, 60, 120]


def test_reserve_is_per_node_and_disabled_when_zero():
    rl = RateLimiter(Clock())
    assert rl.reserve("!a", 1, 60) == 0 and rl.reserve("!b", 1, 60) == 0
    assert all(rl.reserve("!c", 0, 60) == 0 for _ in range(5))
    assert rl.pending("!c") == 0
