"""Offline SQL consistency helpers (decision D-18, REQ-QA-002, REQ-DAT-001, REQ-DAT-005).

``collect_statements`` finds every SQL string handed to ``text(...)`` or
``exec_driver_sql(...)`` in ``services/`` and ``libs/``. ``load_schema`` replays
``migrations/`` into tables, columns and unique keys. ``analyze`` parses one
statement with ``pglast`` (the PostgreSQL parser) and reports every table,
column, INSERT column or ON CONFLICT target that the schema does not back.
Nothing needs a database. Constructs the analyzer cannot judge are reported as
``unsupported`` problems so the tests can list them with a reason.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pglast
from pglast import ast as pg
from pglast import enums
from pglast.stream import RawStream

REPO = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = ("services/", "libs/")
SQL_CALLS = ("text", "exec_driver_sql")
SQL_START = re.compile(
    r"^\s*(select\s|insert\s+into\s|update\s+[\w.]+\s+set\s|delete\s+from\s|with\s+\w+\s+as\b)",
    re.IGNORECASE,
)
SYSTEM_SCHEMAS = frozenset({"pg_catalog", "information_schema"})
# Statements that never change tables, columns or unique keys.
HARMLESS_MIGRATION_STATEMENTS = frozenset(
    {
        "CommentStmt",
        "CreateExtensionStmt",
        "CreateSchemaStmt",
        "DoStmt",
        "InsertStmt",
    }
)
HARMLESS_ALTER_COMMANDS = frozenset(
    {
        enums.AlterTableType.AT_EnableRowSecurity,
        enums.AlterTableType.AT_ForceRowSecurity,
        enums.AlterTableType.AT_ColumnDefault,
        enums.AlterTableType.AT_SetNotNull,
        enums.AlterTableType.AT_DropNotNull,
        enums.AlterTableType.AT_ValidateConstraint,
    }
)


def tracked(*prefixes: str) -> list[str]:
    """List repository files with ``git ls-files`` (never a filesystem walk)."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return sorted(name for name in out if name.startswith(prefixes) and (REPO / name).is_file())


# --------------------------------------------------------------------------- #
# Collecting statements from Python source
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Statement:
    """One SQL string found in Python source.

    ``kind`` is ``literal`` (string constant), ``name`` (a variable resolved to
    a constant), ``fstring`` (rendered with every ``{...}`` removed),
    ``buffered`` (a variable fed from a loop over SQL literals buffered in the
    same class), ``name-fallback`` (a variable fed by a loop; every SQL looking constant of
    the function is checked) or ``unresolved`` (``sql`` is None).
    """

    file: str
    line: int
    call: str
    kind: str
    sql: str | None
    detail: str = ""

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"


def _string_value(node: ast.AST | None) -> str | None:
    """Return the value of a constant string, with ``+`` concatenation folded."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _string_value(node.left), _string_value(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _render_fstring(node: ast.JoinedStr) -> str:
    return "".join(
        part.value if isinstance(part, ast.Constant) else ""
        for part in node.values
        if isinstance(part, ast.Constant | ast.FormattedValue)
    )


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    return name if name in SQL_CALLS else None


class _Collector(ast.NodeVisitor):
    def __init__(self, file: str) -> None:
        self.file = file
        self.found: list[Statement] = []
        self.functions: list[ast.AST] = []
        self.classes: list[ast.ClassDef] = []
        self.module_names: dict[str, ast.AST] = {}

    def run(self, tree: ast.Module) -> list[Statement]:
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    self.module_names[target.id] = node.value
        self.visit(tree)
        return self.found

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.append(node)
        self.generic_visit(node)
        self.classes.pop()

    def visit_Call(self, node: ast.Call) -> None:
        call = _call_name(node)
        if call is not None and node.args:
            self.found.extend(self._resolve(call, node.args[0], node.lineno))
        self.generic_visit(node)

    def _add(self, call: str, line: int, kind: str, sql: str | None, detail: str = "") -> Statement:
        return Statement(self.file, line, call, kind, sql, detail)

    def _resolve(self, call: str, arg: ast.AST, line: int) -> list[Statement]:
        value = _string_value(arg)
        if value is not None:
            return [self._add(call, line, "literal", value)]
        if isinstance(arg, ast.JoinedStr):
            return [self._add(call, line, "fstring", _render_fstring(arg))]
        if isinstance(arg, ast.Name):
            return self._resolve_name(call, arg.id, line)
        return [self._add(call, line, "unresolved", None, ast.dump(arg)[:60])]

    def _resolve_name(self, call: str, name: str, line: int) -> list[Statement]:
        scope = self.functions[-1] if self.functions else None
        if scope is not None:
            assigns = [
                node
                for node in ast.walk(scope)
                if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
            ]
            if len(assigns) == 1:
                value = _string_value(assigns[0].value)
                if value is not None:
                    return [self._add(call, line, "name", value, name)]
        if name in self.module_names:
            value = _string_value(self.module_names[name])
            if value is not None:
                return [self._add(call, line, "name", value, name)]
        if scope is not None:
            found = [
                self._add(call, node.lineno, "name-fallback", node.value, name)
                for node in ast.walk(scope)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and SQL_START.match(node.value)
            ]
            if found:
                return found
        buffered = self._buffered_in_class(call, name)
        if buffered:
            return buffered
        return [self._add(call, line, "unresolved", None, name)]

    def _buffered_in_class(self, call: str, name: str) -> list[Statement]:
        """Follow SQL literals buffered in the same class and later run via ``text(name)``.

        A literal counts when it is the first item of a ``(sql, params)`` tuple or
        the direct argument of ``.append(...)`` anywhere in the enclosing class.
        """
        if not self.classes:
            return []
        found: list[Statement] = []
        seen: set[int] = set()
        for node in ast.walk(self.classes[-1]):
            candidates: list[ast.AST] = []
            if isinstance(node, ast.Tuple) and node.elts:
                candidates.append(node.elts[0])
            elif isinstance(node, ast.Call) and node.args:
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "append":
                    candidates.append(node.args[0])
            for item in candidates:
                value = _string_value(item)
                if value is None or id(item) in seen or not SQL_START.match(value):
                    continue
                seen.add(id(item))
                found.append(self._add(call, item.lineno, "buffered", value, name))
        return found


def collect_statements() -> list[Statement]:
    """Return every SQL string passed to ``text(...)`` or ``exec_driver_sql(...)``."""
    found: list[Statement] = []
    for name in tracked(*SOURCE_ROOTS):
        if not name.endswith(".py"):
            continue
        tree = ast.parse((REPO / name).read_text(encoding="utf-8"), filename=name)
        found.extend(_Collector(name).run(tree))
    return found


# --------------------------------------------------------------------------- #
# Schema built from migrations
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Key:
    """A primary key or unique index; ``predicate`` is set for partial indexes."""

    kind: str
    columns: frozenset[str]
    predicate: str | None = None


@dataclass
class Table:
    name: str
    columns: dict[str, None] = field(default_factory=dict)
    keys: list[Key] = field(default_factory=list)


@dataclass
class Schema:
    tables: dict[str, Table] = field(default_factory=dict)
    ignored: set[str] = field(default_factory=set)
    files: list[str] = field(default_factory=list)


def qualified(rel: Any) -> str:
    return f"{rel.schemaname or 'public'}.{rel.relname}".lower()


def normalize(node: Any) -> str:
    """Deparse an expression without table qualifiers, lower case, one line."""
    _strip_qualifiers(node)
    text_ = RawStream()(node)
    return re.sub(r"\s+", " ", text_).strip().lower()


def _strip_qualifiers(node: Any) -> None:
    if isinstance(node, pg.ColumnRef):
        node.fields = node.fields[-1:]
        return
    for child in _children(node):
        _strip_qualifiers(child)


def _children(node: Any) -> list[Any]:
    out: list[Any] = []
    if isinstance(node, tuple | list):
        for item in node:
            out.extend([item] if isinstance(item, pg.Node) else _children(item))
        return out
    for slot in getattr(node, "__slots__", ()):
        value = getattr(node, slot, None)
        if isinstance(value, pg.Node):
            out.append(value)
        elif isinstance(value, tuple | list):
            out.extend(_children(value))
    return out


def _index_key(params: Any, predicate: Any, kind: str) -> Key:
    columns = frozenset(
        param.name.lower() if param.name else normalize(param.expr) for param in params
    )
    return Key(kind, columns, normalize(predicate) if predicate is not None else None)


def _constraint_key(constraint: Any) -> Key | None:
    kinds = {
        enums.ConstrType.CONSTR_PRIMARY: "primary",
        enums.ConstrType.CONSTR_UNIQUE: "unique",
    }
    kind = kinds.get(constraint.contype)
    if kind is None or not constraint.keys:
        return None
    return Key(kind, frozenset(item.sval.lower() for item in constraint.keys))


def migration_files() -> list[str]:
    """Return migrations in apply order: ``contract_`` files last."""
    names = [name for name in tracked("migrations/") if name.endswith(".sql")]
    return sorted(names, key=lambda n: (Path(n).name.startswith("contract_"), Path(n).name))


def schema_from_sql(sources: list[str]) -> Schema:
    """Build a schema from SQL texts applied in the given order."""
    schema = Schema()
    for source in sources:
        for raw in pglast.parse_sql(source):
            _apply(schema, raw.stmt)
    return schema


def load_schema() -> Schema:
    """Replay ``migrations/`` in order into tables, columns and unique keys."""
    files = migration_files()
    schema = schema_from_sql([(REPO / name).read_text(encoding="utf-8") for name in files])
    schema.files = files
    return schema


def _add_column(table: Table, column: Any) -> None:
    table.columns[column.colname.lower()] = None
    for constraint in column.constraints or ():
        if constraint.contype == enums.ConstrType.CONSTR_PRIMARY:
            table.keys.append(Key("primary", frozenset({column.colname.lower()})))
        elif constraint.contype == enums.ConstrType.CONSTR_UNIQUE:
            table.keys.append(Key("unique", frozenset({column.colname.lower()})))


def _apply(schema: Schema, stmt: Any) -> None:
    kind = type(stmt).__name__
    if isinstance(stmt, pg.CreateStmt):
        table = schema.tables.setdefault(qualified(stmt.relation), Table(qualified(stmt.relation)))
        for inherited in stmt.inhRelations or ():
            table.columns.update(schema.tables[qualified(inherited)].columns)
        for element in stmt.tableElts or ():
            if isinstance(element, pg.ColumnDef):
                _add_column(table, element)
            elif isinstance(element, pg.Constraint):
                key = _constraint_key(element)
                if key is not None:
                    table.keys.append(key)
            elif isinstance(element, pg.TableLikeClause):
                table.columns.update(schema.tables[qualified(element.relation)].columns)
    elif isinstance(stmt, pg.AlterTableStmt):
        table = schema.tables[qualified(stmt.relation)]
        for cmd in stmt.cmds:
            if cmd.subtype == enums.AlterTableType.AT_AddColumn:
                _add_column(table, cmd.def_)
            elif cmd.subtype == enums.AlterTableType.AT_AddConstraint:
                key = _constraint_key(cmd.def_)
                if key is not None:
                    table.keys.append(key)
            elif cmd.subtype == enums.AlterTableType.AT_DropColumn:
                table.columns.pop(cmd.name.lower(), None)
            elif cmd.subtype not in HARMLESS_ALTER_COMMANDS:
                schema.ignored.add(f"AlterTableCmd:{cmd.subtype.name}")
    elif isinstance(stmt, pg.IndexStmt):
        if stmt.unique:
            table = schema.tables[qualified(stmt.relation)]
            table.keys.append(_index_key(stmt.indexParams, stmt.whereClause, "unique-index"))
    elif isinstance(stmt, pg.DropStmt):
        _apply_drop(schema, stmt)
    elif kind not in HARMLESS_MIGRATION_STATEMENTS:
        schema.ignored.add(kind)


def _apply_drop(schema: Schema, stmt: Any) -> None:
    if stmt.removeType == enums.ObjectType.OBJECT_SCHEMA:
        for item in stmt.objects:
            prefix = item.sval.lower() + "."
            for name in [t for t in schema.tables if t.startswith(prefix)]:
                del schema.tables[name]
    elif stmt.removeType == enums.ObjectType.OBJECT_TABLE:
        for parts in stmt.objects:
            names = [item.sval.lower() for item in parts]
            schema.tables.pop(f"{names[-2] if len(names) > 1 else 'public'}.{names[-1]}", None)
    elif stmt.removeType != enums.ObjectType.OBJECT_INDEX:
        schema.ignored.add(f"DropStmt:{stmt.removeType.name}")


# --------------------------------------------------------------------------- #
# Statement analysis
# --------------------------------------------------------------------------- #

_BIND = re.compile(
    r"'(?:[^']|'')*'|--[^\n]*|\"[^\"]*\"|::|:([A-Za-z_]\w*)",
)


def replace_binds(sql: str) -> str:
    """Replace SQLAlchemy ``:name`` binds with numbered ``$n`` placeholders."""
    numbers: dict[str, int] = {}

    def swap(match: re.Match[str]) -> str:
        name = match.group(1)
        if name is None:
            return match.group(0)
        return f"${numbers.setdefault(name, len(numbers) + 1)}"

    return _BIND.sub(swap, sql)


@dataclass(frozen=True)
class Problem:
    """A finding. ``kind`` is parse, table, column, insert-column, conflict or unsupported."""

    kind: str
    message: str


@dataclass
class Source:
    """A FROM item: ``columns`` is None when its columns cannot be known."""

    alias: str
    columns: set[str] | None
    table: str | None = None


Scope = list[Source]
Chain = list[Scope]
Ctes = dict[str, Source]


class _Analyzer:
    def __init__(self, schema: Schema) -> None:
        self.schema = schema
        self.problems: list[Problem] = []

    def problem(self, kind: str, message: str) -> None:
        item = Problem(kind, message)
        if item not in self.problems:
            self.problems.append(item)

    # -- relations -------------------------------------------------------- #

    def table_source(self, rel: Any, ctes: Ctes, alias: str | None = None) -> Source:
        label = alias or rel.relname
        if rel.schemaname is None and rel.relname.lower() in ctes:
            cte = ctes[rel.relname.lower()]
            return Source(label.lower(), cte.columns, None)
        schema_name = (rel.schemaname or "public").lower()
        if schema_name in SYSTEM_SCHEMAS or (
            rel.schemaname is None and rel.relname.lower().startswith("pg_")
        ):
            return Source(label.lower(), None, None)
        table = self.schema.tables.get(qualified(rel))
        if table is None:
            self.problem("table", f"table {qualified(rel)} does not exist")
            return Source(label.lower(), None, None)
        return Source(label.lower(), set(table.columns), table.name)

    def rangevar_source(self, rel: Any, ctes: Ctes) -> Source:
        alias = rel.alias.aliasname if rel.alias else None
        source = self.table_source(rel, ctes, alias)
        if rel.alias and rel.alias.colnames and source.columns is not None:
            source.columns = {item.sval.lower() for item in rel.alias.colnames}
        return source

    # -- queries ---------------------------------------------------------- #

    def query(self, node: Any, chain: Chain, ctes: Ctes) -> list[str] | None:
        """Analyze a query node; return its output column names (None if unknown)."""
        if isinstance(node, pg.SelectStmt):
            return self.select(node, chain, ctes)
        if isinstance(node, pg.InsertStmt):
            return self.insert(node, chain, ctes)
        if isinstance(node, pg.UpdateStmt):
            return self.update(node, chain, ctes)
        if isinstance(node, pg.DeleteStmt):
            return self.delete(node, chain, ctes)
        self.problem("unsupported", f"statement type {type(node).__name__}")
        return None

    def with_clause(self, clause: Any, chain: Chain, ctes: Ctes) -> Ctes:
        ctes = dict(ctes)
        for cte in clause.ctes if clause else ():
            name = cte.ctename.lower()
            if clause.recursive:
                ctes[name] = Source(name, None)
            outputs = self.query(cte.ctequery, chain, ctes)
            if cte.aliascolnames:
                outputs = [item.sval.lower() for item in cte.aliascolnames]
            ctes[name] = Source(name, set(outputs) if outputs is not None else None)
        return ctes

    def select(self, node: pg.SelectStmt, chain: Chain, ctes: Ctes) -> list[str] | None:
        ctes = self.with_clause(node.withClause, chain, ctes)
        if node.op != enums.SetOperation.SETOP_NONE:
            left = self.query(node.larg, chain, ctes)
            self.query(node.rarg, chain, ctes)
            for sort in node.sortClause or ():
                self.expression(
                    sort, [[Source("", set(left or ()) if left else None)]] + chain, ctes
                )
            return left
        if node.valuesLists:
            scope: Scope = []
            for row in node.valuesLists:
                self.expression(row, [scope, *chain], ctes)
            return None
        scope = []
        local = [scope, *chain]
        for item in node.fromClause or ():
            self.from_item(item, scope, local, ctes)
        outputs = self.targets(node.targetList, scope, local, ctes)
        output_names = set(outputs or ())
        for part in (node.whereClause, node.havingClause, node.windowClause):
            self.expression(part, local, ctes)
        for part in (node.groupClause, node.sortClause, node.distinctClause):
            self.expression(part, local + [[Source("", output_names)]], ctes, outputs=True)
        self.expression(node.limitCount, local, ctes)
        self.expression(node.limitOffset, local, ctes)
        return outputs

    def targets(self, targets: Any, scope: Scope, chain: Chain, ctes: Ctes) -> list[str] | None:
        names: list[str] | None = []
        for target in targets or ():
            value = target.val
            if isinstance(value, pg.ColumnRef) and isinstance(value.fields[-1], pg.A_Star):
                star = self.star(value, scope)
                names = None if names is None or star is None else names + star
                continue
            self.expression(value, chain, ctes)
            name = self.output_name(target)
            if names is not None:
                names.append(name)
        return names

    def star(self, ref: Any, scope: Scope) -> list[str] | None:
        qualifier = ref.fields[-2].sval.lower() if len(ref.fields) > 1 else None
        cols: list[str] = []
        matched = False
        for source in scope:
            if qualifier is not None and source.alias != qualifier:
                continue
            matched = True
            if source.columns is None:
                return None
            cols.extend(sorted(source.columns))
        if qualifier is not None and not matched:
            self.problem("table", f"alias {qualifier} in {qualifier}.* is not in scope")
        return cols

    @staticmethod
    def output_name(target: Any) -> str:
        if target.name:
            return target.name.lower()
        value = target.val
        if isinstance(value, pg.ColumnRef) and isinstance(value.fields[-1], pg.String):
            return value.fields[-1].sval.lower()
        if isinstance(value, pg.FuncCall):
            return value.funcname[-1].sval.lower()
        return "?column?"

    def from_item(self, item: Any, scope: Scope, chain: Chain, ctes: Ctes) -> None:
        if isinstance(item, pg.RangeVar):
            scope.append(self.rangevar_source(item, ctes))
        elif isinstance(item, pg.JoinExpr):
            self.from_item(item.larg, scope, chain, ctes)
            self.from_item(item.rarg, scope, chain, ctes)
            self.expression(item.quals, chain, ctes)
            if item.alias:
                scope.append(Source(item.alias.aliasname.lower(), None))
        elif isinstance(item, pg.RangeSubselect):
            outputs = self.query(item.subquery, chain, ctes)
            alias = item.alias.aliasname.lower() if item.alias else ""
            if item.alias and item.alias.colnames:
                outputs = [name.sval.lower() for name in item.alias.colnames]
            scope.append(Source(alias, set(outputs) if outputs is not None else None))
        elif isinstance(item, pg.RangeFunction):
            self.expression(item.functions, chain, ctes)
            alias = item.alias.aliasname.lower() if item.alias else ""
            cols: set[str] | None = None
            if item.alias and item.alias.colnames:
                cols = {name.sval.lower() for name in item.alias.colnames}
            elif item.coldeflist:
                cols = {column.colname.lower() for column in item.coldeflist}
            scope.append(Source(alias, cols))
        else:
            self.problem("unsupported", f"FROM item {type(item).__name__}")
            scope.append(Source("", None))

    # -- expressions ------------------------------------------------------ #

    def expression(self, node: Any, chain: Chain, ctes: Ctes, outputs: bool = False) -> None:
        if node is None:
            return
        if isinstance(node, tuple | list):
            for item in node:
                self.expression(item, chain, ctes, outputs)
            return
        if isinstance(node, pg.ColumnRef):
            self.column_ref(node, chain, outputs)
        elif isinstance(node, pg.SubLink):
            self.expression(node.testexpr, chain, ctes)
            self.query(node.subselect, chain, ctes)
        elif isinstance(node, pg.SelectStmt | pg.InsertStmt | pg.UpdateStmt | pg.DeleteStmt):
            self.query(node, chain, ctes)
        elif isinstance(node, pg.LockingClause):
            return
        elif isinstance(node, pg.Node):
            for child in _children(node):
                self.expression(child, chain, ctes, outputs)

    def column_ref(self, ref: Any, chain: Chain, outputs: bool) -> None:
        fields = ref.fields
        if isinstance(fields[-1], pg.A_Star):
            if len(fields) > 1:
                qualifier = fields[-2].sval.lower()
                if not any(s.alias == qualifier for scope in chain for s in scope):
                    self.problem("table", f"alias {qualifier} in {qualifier}.* is not in scope")
            return
        column = fields[-1].sval.lower()
        if len(fields) == 1:
            for scope in chain:
                for source in scope:
                    if source.columns is None or column in source.columns:
                        return
            self.problem("column", f"column {column} is not in any table in scope")
            return
        qualifier = fields[-2].sval.lower()
        for scope in chain:
            for source in scope:
                if source.alias == qualifier or source.table == f"{qualifier}":
                    if source.columns is None or column in source.columns:
                        return
                    where = source.table or source.alias
                    self.problem("column", f"column {column} does not exist in {where}")
                    return
        if len(fields) >= 2:
            # schema.table.column or composite field access
            for scope in chain:
                for source in scope:
                    if source.table and source.table.endswith(f".{qualifier}"):
                        if source.columns is None or column in source.columns:
                            return
                        self.problem("column", f"column {column} does not exist in {source.table}")
                        return
        self.problem("table", f"alias {qualifier} is not in scope for column {column}")

    # -- DML -------------------------------------------------------------- #

    def target(self, rel: Any, ctes: Ctes) -> Source:
        return self.rangevar_source(rel, ctes)

    def returning(self, clause: Any, scope: Scope, chain: Chain, ctes: Ctes) -> list[str] | None:
        if clause is None:
            return None
        local = [scope, *chain]
        names: list[str] | None = []
        for item in clause.exprs:
            value = item.val
            if isinstance(value, pg.ColumnRef) and isinstance(value.fields[-1], pg.A_Star):
                star = self.star(value, scope)
                names = None if names is None or star is None else names + star
                continue
            self.expression(value, local, ctes)
            if names is not None:
                names.append(self.output_name(item))
        return names

    def columns_exist(self, names: list[str], source: Source, kind: str, label: str) -> None:
        if source.columns is None:
            return
        for name in names:
            if name.lower() not in source.columns:
                self.problem(kind, f"{label} column {name} does not exist in {source.table}")

    def insert(self, node: pg.InsertStmt, chain: Chain, ctes: Ctes) -> list[str] | None:
        ctes = self.with_clause(node.withClause, chain, ctes)
        source = self.target(node.relation, ctes)
        names = [col.name for col in node.cols or ()]
        self.columns_exist(names, source, "insert-column", "INSERT")
        if node.selectStmt is not None:
            self.query(node.selectStmt, chain, ctes)
        conflict = node.onConflictClause
        if conflict is not None:
            self.on_conflict(conflict, node, source, chain, ctes)
        return self.returning(node.returningClause, [source], chain, ctes)

    def on_conflict(
        self, conflict: Any, node: pg.InsertStmt, source: Source, chain: Chain, ctes: Ctes
    ) -> None:
        excluded = Source("excluded", set(source.columns) if source.columns is not None else None)
        local: Chain = [[source, excluded], *chain]
        infer = conflict.infer
        if infer is not None:
            for elem in infer.indexElems or ():
                if elem.name:
                    self.columns_exist([elem.name], source, "column", "ON CONFLICT")
                else:
                    self.expression(elem.expr, local, ctes)
            self.expression(infer.whereClause, local, ctes)
        for target in conflict.targetList or ():
            self.columns_exist([target.name], source, "column", "ON CONFLICT SET")
            self.expression(target.val, local, ctes)
        self.expression(conflict.whereClause, local, ctes)

    def update(self, node: pg.UpdateStmt, chain: Chain, ctes: Ctes) -> list[str] | None:
        ctes = self.with_clause(node.withClause, chain, ctes)
        source = self.target(node.relation, ctes)
        scope: Scope = [source]
        local = [scope, *chain]
        for item in node.fromClause or ():
            self.from_item(item, scope, local, ctes)
        self.columns_exist([t.name for t in node.targetList], source, "column", "UPDATE SET")
        for target in node.targetList:
            self.expression(target.val, local, ctes)
        self.expression(node.whereClause, local, ctes)
        return self.returning(node.returningClause, scope, chain, ctes)

    def delete(self, node: pg.DeleteStmt, chain: Chain, ctes: Ctes) -> list[str] | None:
        ctes = self.with_clause(node.withClause, chain, ctes)
        source = self.target(node.relation, ctes)
        scope: Scope = [source]
        local = [scope, *chain]
        for item in node.usingClause or ():
            self.from_item(item, scope, local, ctes)
        self.expression(node.whereClause, local, ctes)
        return self.returning(node.returningClause, scope, chain, ctes)


def analyze(sql: str, schema: Schema) -> list[Problem]:
    """Parse ``sql`` and return every schema inconsistency found in it."""
    try:
        raw = pglast.parse_sql(replace_binds(sql))
    except pglast.parser.ParseError as error:
        return [Problem("parse", str(error))]
    analyzer = _Analyzer(schema)
    for item in raw:
        analyzer.query(item.stmt, [], {})
        stmt = item.stmt
        if isinstance(stmt, pg.InsertStmt) and stmt.onConflictClause is not None:
            _check_conflict(analyzer, stmt, schema)
    return analyzer.problems


def _check_conflict(analyzer: _Analyzer, stmt: Any, schema: Schema) -> None:
    infer = stmt.onConflictClause.infer
    table = schema.tables.get(qualified(stmt.relation))
    if table is None:
        return
    if infer is None:
        if stmt.onConflictClause.action == enums.OnConflictAction.ONCONFLICT_UPDATE:
            analyzer.problem("conflict", "DO UPDATE needs a conflict target")
        return
    if infer.conname:
        analyzer.problem("unsupported", f"ON CONFLICT ON CONSTRAINT {infer.conname}")
        return
    columns = frozenset(
        elem.name.lower() if elem.name else normalize(elem.expr) for elem in infer.indexElems
    )
    predicate = normalize(infer.whereClause) if infer.whereClause is not None else None
    candidates = [key for key in table.keys if key.columns == columns]
    label = f"ON CONFLICT ({', '.join(sorted(columns))}) on {table.name}"
    if not candidates:
        analyzer.problem("conflict", f"{label} matches no primary key or unique index")
    elif not any(key.predicate is None or key.predicate == predicate for key in candidates):
        analyzer.problem(
            "conflict",
            f"{label} matches only a partial index; repeat its predicate "
            f"({candidates[0].predicate}) after the target",
        )
