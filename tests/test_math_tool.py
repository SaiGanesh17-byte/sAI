from tools.math import MathTool


def test_evaluate_numeric_expression():
    result = MathTool().execute({"operation": "evaluate", "expression": "2**10 + 4"})
    assert "1028" in result


def test_solve_quadratic_for_variable():
    result = MathTool().execute({"operation": "solve", "expression": "x**2 - 5*x + 6", "variable": "x"})
    assert "2" in result and "3" in result


def test_differentiate_polynomial():
    result = MathTool().execute({"operation": "differentiate", "expression": "x**3 + 2*x", "variable": "x"})
    assert "3*x**2 + 2" in result


def test_integrate_polynomial():
    result = MathTool().execute({"operation": "integrate", "expression": "2*x", "variable": "x"})
    assert "x**2" in result


def test_matrix_determinant():
    result = MathTool().execute({"operation": "matrix_det", "expression": "[[1,2],[3,4]]"})
    assert "-2" in result


def test_matrix_inverse_of_singular_matrix_reports_error_instead_of_crashing():
    result = MathTool().execute({"operation": "matrix_inverse", "expression": "[[1,2],[2,4]]"})
    assert result.startswith("Error")


def test_stats_summary():
    result = MathTool().execute({"operation": "stats", "values": [1, 2, 3, 4, 5]})
    assert "mean = 3" in result
    assert "median = 3" in result


def test_stats_requires_values():
    result = MathTool().execute({"operation": "stats"})
    assert result.startswith("Error")


def test_unparseable_expression_is_reported_not_raised():
    # Must never fall through to a bare eval() of arbitrary input -- an
    # unparseable/non-math expression should come back as a plain error
    # string, not raise, and never execute the input as Python.
    result = MathTool().execute({"operation": "evaluate", "expression": "import os"})
    assert result.startswith("Error")


def test_unknown_operation_is_reported():
    result = MathTool().execute({"operation": "bogus_op", "expression": "1+1"})
    assert result.startswith("Error")
