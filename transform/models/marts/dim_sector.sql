-- Sector dimension (CIIU code plus own taxonomy). Derived from the sector codes
-- present on companies until a curated taxonomy table exists.

select distinct
    sector_code,
    sector_code as sector_label
from {{ ref('stg_core__company') }}
where sector_code is not null
