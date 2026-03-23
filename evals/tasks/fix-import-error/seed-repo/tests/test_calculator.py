"""Tests for the calculator module."""

from calculator import Calculator


def test_add():
    calc = Calculator()
    assert calc.add(2, 3) == 5


def test_add_negative():
    calc = Calculator()
    assert calc.add(-1, 1) == 0


def test_multiply():
    calc = Calculator()
    assert calc.multiply(3, 4) == 12


def test_multiply_by_zero():
    calc = Calculator()
    assert calc.multiply(5, 0) == 0


def test_validate_rejects_string():
    import pytest
    calc = Calculator()
    with pytest.raises(TypeError):
        calc.add("a", 1)
