{# Keep this project's dbt models in analytics without duplicating the schema as
   analytics_analytics. Relates to dbt_project.yml and staging/mart models; it
   makes Analytics reads find the exact tables created by the migration. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
