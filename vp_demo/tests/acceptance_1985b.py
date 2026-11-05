"""Run the example displayed in the room; standalone local exercise, never submitted to CF."""
import sys


def best_multiple(n):
    return max(range(2, n + 1), key=lambda x: sum(range(x, n + 1, x)))


if __name__ == "__main__":
    t, *cases = map(int, sys.stdin.read().split())
    if len(cases) != t:
        raise SystemExit("Unexpected number of test cases")
    for n in cases:
        print(best_multiple(n))
