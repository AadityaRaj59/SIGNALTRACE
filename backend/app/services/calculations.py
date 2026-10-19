from decimal import Decimal, InvalidOperation

class CalculationError(ValueError):
    pass

def percentage_change(start_value, end_value, *, unit, start_period, end_period, start_basis, end_basis):
    if not str(unit).strip():
        raise CalculationError("Unit is required.")
    if start_period == end_period:
        raise CalculationError("Start and end periods must differ.")
    if start_basis.strip().lower() != end_basis.strip().lower():
        raise CalculationError("Reporting bases differ; comparison is unsafe.")
    try:
        start, end = Decimal(str(start_value)), Decimal(str(end_value))
    except InvalidOperation as exc:
        raise CalculationError("Values must be numeric.") from exc
    if start == 0:
        raise CalculationError("Percentage change is undefined when the starting value is zero.")
    result = ((end - start) / abs(start)) * Decimal("100")
    return {"name":"Percentage change", "formula":"((end - start) / abs(start)) * 100",
            "start_value":str(start), "end_value":str(end), "unit":unit,
            "start_period":start_period, "end_period":end_period, "reporting_basis":start_basis,
            "result_percent":str(result.quantize(Decimal("0.01")))}

def basis_point_change(start_percent, end_percent, *, start_period, end_period, start_basis, end_basis):
    if start_period == end_period:
        raise CalculationError("Start and end periods must differ.")
    if start_basis.strip().lower() != end_basis.strip().lower():
        raise CalculationError("Reporting bases differ; comparison is unsafe.")
    start, end = Decimal(str(start_percent)), Decimal(str(end_percent))
    return {"name":"Basis-point change", "formula":"(end percentage - start percentage) * 100",
            "start_percent":str(start), "end_percent":str(end), "start_period":start_period,
            "end_period":end_period, "reporting_basis":start_basis,
            "result_basis_points":str(((end-start)*Decimal("100")).quantize(Decimal("0.01")))}
