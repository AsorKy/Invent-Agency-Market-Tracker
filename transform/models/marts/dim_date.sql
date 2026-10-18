-- Calendar dimension, 2020-01-01 through 2039-12-31. Also serves as the
-- MetricFlow time spine (see metricflow_time_spine.sql).

select
    d::date as date_day,
    extract(year from d)::int as year,
    extract(quarter from d)::int as quarter,
    extract(month from d)::int as month,
    extract(isodow from d)::int as iso_day_of_week,
    extract(isodow from d) in (6, 7) as is_weekend
from generate_series('2020-01-01'::date, '2039-12-31'::date, interval '1 day') as g(d)
