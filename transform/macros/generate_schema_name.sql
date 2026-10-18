{# Keep this project's dbt models in the custom schema (pulse) without
   prefixing it with the target schema (for example public_pulse). Relates to
   dbt_project.yml and the staging and mart models. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
