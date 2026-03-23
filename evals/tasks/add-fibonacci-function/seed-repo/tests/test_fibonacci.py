"""Tests for the fibonacci function."""

import time
import pytest
from math_utils import fibonacci


class TestFibonacciBaseCases:
    def test_fib_0(self):
        assert fibonacci(0) == 0

    def test_fib_1(self):
        assert fibonacci(1) == 1

    def test_fib_2(self):
        assert fibonacci(2) == 1


class TestFibonacciSequence:
    def test_fib_10(self):
        assert fibonacci(10) == 55

    def test_fib_20(self):
        assert fibonacci(20) == 6765

    def test_fib_sequence(self):
        """First 10 Fibonacci numbers."""
        expected = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
        actual = [fibonacci(i) for i in range(10)]
        assert actual == expected


class TestFibonacciEdgeCases:
    def test_negative_raises_value_error(self):
        with pytest.raises(ValueError):
            fibonacci(-1)

    def test_negative_large_raises_value_error(self):
        with pytest.raises(ValueError):
            fibonacci(-100)

    def test_returns_int(self):
        result = fibonacci(10)
        assert isinstance(result, int)


class TestFibonacciPerformance:
    def test_fib_100_is_fast(self):
        """fib(100) should complete in under 1 second (requires memoization)."""
        start = time.monotonic()
        result = fibonacci(100)
        elapsed = time.monotonic() - start

        assert elapsed < 1.0, f"fib(100) took {elapsed:.2f}s — too slow (no memoization?)"
        assert result == 354224848179261915075

    def test_fib_50(self):
        assert fibonacci(50) == 12586269025
