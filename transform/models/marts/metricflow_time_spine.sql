-- Day-grain time spine required by MetricFlow.

select date_day
from {{ ref('mart_dim_date') }}
