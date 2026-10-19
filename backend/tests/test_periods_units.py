from decimal import Decimal
from backend.app.services.periods import parse_period, prior_year_id, prior_quarter_id
from backend.app.services.units import parse_number, normalize_amount
from backend.app.services.metrics import normalize_metric, canonical_metric_id

def test_indian_fiscal_periods_and_previous_period_ids():
    q=parse_period('Q1 FY25')
    assert q['start']=='2024-04-01' and q['end']=='2024-06-30'
    assert prior_year_id(q)=='FY24Q1' and prior_quarter_id(q)=='FY24Q4'
    assert parse_period('1QFY25')['period_id']=='FY25Q1'
    assert parse_period('FY 2024-25')['start']=='2024-04-01'
    assert parse_period('9M FY25')['end']=='2024-12-31'
    assert parse_period('H1 FY26')['start']=='2025-04-01'
    assert parse_period('Jun-25') is None
    assert parse_period('Jun-25', caption='quarter ended')['period_type']=='quarter'
    assert parse_period('quarter ended June 30, 2025')['end']=='2025-06-30'
    assert parse_period('year ended March 31, 2025')['end']=='2025-03-31'
    assert parse_period('as at 31 March 2025')['period_type']=='point_in_time'

def test_decimal_number_and_units_are_conservative():
    assert parse_number('1,23,456.78') == Decimal('123456.78')
    assert parse_number('(1,234.5)') == Decimal('-1234.5')
    assert parse_number('−12.5') == Decimal('-12.5')
    assert normalize_amount('1.2','lakh crore')['value']=='1200000000000'
    assert normalize_amount('5','million')['value'] is None
    assert normalize_amount('5','million','USD millions')['currency']=='USD'
    assert normalize_amount('2.5','%')['unit']=='%'
    assert normalize_amount('25','bps')['unit']=='bps'

def test_metric_aliases_do_not_equate_aum_with_advances():
    assert normalize_metric('Total advances')=='Advances'
    assert normalize_metric('Assets under management')=='Assets under management'
    assert canonical_metric_id('Total deposits')=='total_deposits'
    assert normalize_metric('Gross NPA','INR crore')=='Gross NPA amount'
    assert normalize_metric('Gross NPA','%')=='Gross NPA ratio'
