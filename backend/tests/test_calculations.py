import pytest
from backend.app.services.calculations import percentage_change, basis_point_change, CalculationError

def test_percentage_change():
    x=percentage_change("100","125",unit="INR crore",start_period="FY24",end_period="FY25",start_basis="consolidated",end_basis="consolidated")
    assert x["result_percent"]=="25.00"

def test_zero_start_rejected():
    with pytest.raises(CalculationError):
        percentage_change("0","1",unit="INR",start_period="FY24",end_period="FY25",start_basis="s",end_basis="s")

def test_reporting_basis_mismatch_rejected():
    with pytest.raises(CalculationError):
        percentage_change("1","2",unit="INR",start_period="FY24",end_period="FY25",start_basis="standalone",end_basis="consolidated")

def test_basis_points():
    x=basis_point_change("3.1","2.8",start_period="Q1",end_period="Q2",start_basis="consolidated",end_basis="consolidated")
    assert x["result_basis_points"]=="-30.00"

def test_same_period_rejected():
    with pytest.raises(CalculationError):
        percentage_change("10", "12", unit="INR crore", start_period="FY25", end_period="FY25", start_basis="consolidated", end_basis="consolidated")

def test_negative_values_are_supported_without_dividing_by_signed_start():
    result = percentage_change("-10", "-5", unit="INR crore", start_period="FY24", end_period="FY25", start_basis="consolidated", end_basis="consolidated")
    assert result["result_percent"] == "50.00"

def test_trend_calculation_requires_clear_numeric_values_and_periods():
    from backend.app.services.evidence_engine import calculate_observation_trends
    observations = [
        {"id":"obs_1","metric_name":"Advances","raw_value":"100","unit":"INR crore","period":"FY 2024","reporting_basis":"consolidated","validation_status":"source_excerpt_verified","source_id":1},
        {"id":"obs_2","metric_name":"Advances","raw_value":"120","unit":"INR crore","period":"FY 2025","reporting_basis":"consolidated","validation_status":"source_excerpt_verified","source_id":2},
    ]
    calculations, limitations = calculate_observation_trends(observations)
    assert calculations[0]["result_percent"] == "20.00"
    assert calculations[0]["input_observation_ids"] == ["obs_1", "obs_2"]

def test_evidence_score_never_calls_unreviewed_counter_search_strong():
    import asyncio
    from backend.app.services.evidence_engine import score_hypothesis
    obs = [
        {"id":"obs_1", "source_id":1},
        {"id":"obs_2", "source_id":2},
    ]
    h = {"supporting_observation_ids":["obs_1","obs_2"], "opposing_observation_ids":[],
         "calculation_ids":["calc_1"], "falsification_condition":"A specific measured threshold is crossed.",
         "counter_search_results":[{"url":"https://example.org"}]}
    scored = asyncio.run(score_hypothesis(h, obs, [{"id":"calc_1"}]))
    assert scored["evidence_strength"] == "Weak"  # missing provenance and unresolved counter-evidence cannot earn Moderate
    assert "confidence_breakdown" in scored and scored["confidence_score"] < 0.65

def test_cagr_requires_three_compatible_periods_and_is_deterministic():
    from backend.app.services.evidence_engine import calculate_observation_trends
    observations=[
        {"id":"o1","metric_name":"Advances","metric_canonical":"Advances","raw_value":"100","unit":"INR crore","period":"FY 2022","reporting_basis":"consolidated","validation_status":"source_excerpt_verified","source_id":1},
        {"id":"o2","metric_name":"Advances","metric_canonical":"Advances","raw_value":"121","unit":"INR crore","period":"FY 2023","reporting_basis":"consolidated","validation_status":"source_excerpt_verified","source_id":1},
        {"id":"o3","metric_name":"Advances","metric_canonical":"Advances","raw_value":"144","unit":"INR crore","period":"FY 2024","reporting_basis":"consolidated","validation_status":"source_excerpt_verified","source_id":1},
    ]
    calculations,limits=calculate_observation_trends(observations)
    cagr=next(x for x in calculations if x['kind']=='cagr')
    assert cagr['result_percent']=='20.00'
    assert cagr['input_observation_ids']==['o1','o3']

def test_derived_credit_deposit_ratio_uses_same_period_compatible_values():
    from backend.app.services.evidence_engine import calculate_observation_trends
    observations=[
        {"id":"a1","metric_name":"Advances","metric_canonical":"Advances","raw_value":"800","unit":"INR crore","period":"Q1 FY25","period_type":"quarter","reporting_basis":"consolidated","validation_status":"source_excerpt_verified","source_id":1},
        {"id":"d1","metric_name":"Deposits","metric_canonical":"Deposits","raw_value":"1000","unit":"INR crore","period":"Q1 FY25","period_type":"quarter","reporting_basis":"consolidated","validation_status":"source_excerpt_verified","source_id":1},
    ]
    calculations,limits=calculate_observation_trends(observations)
    derived=next(x for x in calculations if x['kind']=='derived_ratio')
    assert derived['result_percent']=='80.00'
    assert derived['input_observation_ids']==['a1','d1']

def test_unknown_amount_units_are_not_compared():
    from backend.app.services.evidence_engine import calculate_observation_trends
    observations=[
        {"id":"o1","metric_name":"Advances","raw_value":"100","unit":"unknown","period":"FY 2024","reporting_basis":"consolidated","validation_status":"source_excerpt_verified","source_id":1},
        {"id":"o2","metric_name":"Advances","raw_value":"120","unit":"unknown","period":"FY 2025","reporting_basis":"consolidated","validation_status":"source_excerpt_verified","source_id":1},
    ]
    calculations,limits=calculate_observation_trends(observations)
    assert calculations==[]
    assert any('unit is unknown' in x for x in limits)
