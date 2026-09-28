from typing import Dict, Any
from tools.base import BaseTool


class MathTool(BaseTool):
    """
    Symbolic/numeric computation via sympy. Parses expressions through
    sympy.sympify (no bare eval()) so agent-supplied input can't execute
    arbitrary Python -- only build a symbolic math expression tree.
    """

    @property
    def name(self) -> str:
        return "math_solve"

    @property
    def description(self) -> str:
        return (
            "Performs exact symbolic/numeric math: evaluate, simplify, factor, expand, "
            "solve an equation for a variable, differentiate, integrate, or compute "
            "determinant/inverse/eigenvalues of a matrix. Use this instead of hand-calculating "
            "or writing a throwaway script for anything beyond trivial arithmetic."
        )

    @property
    def permissions(self) -> list:
        return []

    @property
    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "evaluate", "simplify", "factor", "expand",
                        "solve", "differentiate", "integrate",
                        "matrix_det", "matrix_inverse", "matrix_eigenvalues",
                        "stats",
                    ],
                    "description": "Which math operation to perform.",
                },
                "expression": {
                    "type": "string",
                    "description": "Math expression in standard notation, e.g. 'x**2 + 3*x - 4' or '[[1,2],[3,4]]' for matrix ops.",
                },
                "variable": {
                    "type": "string",
                    "description": "Variable to solve/differentiate/integrate with respect to (default 'x').",
                },
                "values": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Numeric list for the 'stats' operation.",
                },
            },
            "required": ["operation"],
        }

    def execute(self, args: Dict[str, Any]) -> str:
        import sympy
        from sympy import Matrix, sympify, symbols, diff, integrate, factor, expand, simplify, solve
        from sympy.core.sympify import SympifyError

        operation = args.get("operation", "")
        expression = args.get("expression", "")
        var_name = args.get("variable", "x") or "x"

        try:
            if operation == "stats":
                values = args.get("values") or []
                if not values:
                    return "Error: 'values' (a non-empty numeric array) is required for the 'stats' operation."
                import statistics
                n = len(values)
                mean = statistics.mean(values)
                result_lines = [
                    f"n = {n}",
                    f"mean = {mean}",
                    f"median = {statistics.median(values)}",
                    f"stdev = {statistics.stdev(values) if n > 1 else 0}",
                    f"variance = {statistics.variance(values) if n > 1 else 0}",
                    f"min = {min(values)}",
                    f"max = {max(values)}",
                    f"sum = {sum(values)}",
                ]
                return "\n".join(result_lines)

            if not expression:
                return "Error: 'expression' is required for this operation."

            if operation in ("matrix_det", "matrix_inverse", "matrix_eigenvalues"):
                try:
                    data = sympify(expression)
                    mat = Matrix(data)
                except Exception as e:
                    return f"Error: could not parse '{expression}' as a matrix (expected e.g. '[[1,2],[3,4]]'): {e}"

                if operation == "matrix_det":
                    return f"det = {mat.det()}"
                if operation == "matrix_inverse":
                    if mat.det() == 0:
                        return "Error: matrix is singular (determinant is 0); it has no inverse."
                    return f"inverse =\n{mat.inv()}"
                if operation == "matrix_eigenvalues":
                    return f"eigenvalues = {mat.eigenvals()}"

            var = symbols(var_name)
            try:
                sym_expr = sympify(expression)
            except (SympifyError, Exception) as e:
                return f"Error: could not parse expression '{expression}': {e}"

            if operation == "evaluate":
                return f"result = {sympy.N(sym_expr)}"
            if operation == "simplify":
                return f"simplified = {simplify(sym_expr)}"
            if operation == "factor":
                return f"factored = {factor(sym_expr)}"
            if operation == "expand":
                return f"expanded = {expand(sym_expr)}"
            if operation == "differentiate":
                return f"d/d{var_name} = {diff(sym_expr, var)}"
            if operation == "integrate":
                return f"integral = {integrate(sym_expr, var)} + C"
            if operation == "solve":
                solutions = solve(sym_expr, var)
                if not solutions:
                    return f"No solutions found for '{expression}' = 0 in terms of {var_name}."
                return f"{var_name} = {solutions}"

            return f"Error: unknown operation '{operation}'. See schema for supported operations."
        except Exception as e:
            return f"Error during math computation: {e}"
