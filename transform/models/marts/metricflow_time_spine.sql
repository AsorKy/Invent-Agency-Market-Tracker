-- Day-grain time spine required by MetricFlow.

select date_day
from {{ ref('dim_date') }}
