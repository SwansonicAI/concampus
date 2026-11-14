"""SQLite for development and the tests, taught what Postgres has: `fold` for forgiving search and German order
(core/text.py). A column's collation is named, quoted, as Postgres names it, so both take the same migrations."""
from django.db.backends.sqlite3 import base, schema

from core.text import COLLATION, fold, german


class SchemaEditor(schema.DatabaseSchemaEditor):
    def _collate_sql(self, collation):
        return 'COLLATE ' + self.quote_name(collation)


class DatabaseWrapper(base.DatabaseWrapper):
    SchemaEditorClass = SchemaEditor

    def get_new_connection(self, conn_params):
        connection = super().get_new_connection(conn_params)
        connection.create_function('fold', 1, fold, deterministic=True)
        connection.create_collation(COLLATION, german)
        return connection
