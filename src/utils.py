import csv
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_DB_PATH = "data/financial.sqlite"
DEFAULT_DESCRIPTION_DIR = "data/database_description"


def load_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """
    Load the SQLite database and return a connection.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        sqlite3.Connection: Active database connection

    Raises:
        FileNotFoundError: If the database file doesn't exist
        sqlite3.Error: If there's an error connecting to the database
    """
    db_file = Path(db_path)
    if not db_file.exists():
        raise FileNotFoundError(
            f"Database file not found: {db_path}\n" "Please run setup.sh first to download the database."
        )

    try:
        database_uri = f"{db_file.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(database_uri, uri=True)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        raise sqlite3.Error(f"Error connecting to database: {e}")


def query_db(
    conn: sqlite3.Connection,
    query: str,
    params: Optional[tuple] = None,
    max_rows: int = 10_000,
) -> List[Dict[str, Any]]:
    """
    Execute a bounded metadata query and return dictionaries.

    Args:
        conn: Active SQLite database connection
        query: SQL query string to execute
        params: Optional tuple of parameters for parameterized queries
        max_rows: Hard result limit for metadata operations

    Returns:
        List[Dict[str, Any]]: Query results

    Raises:
        sqlite3.Error: If there's an error executing the query
    """
    try:
        if max_rows < 1:
            raise ValueError("max_rows must be positive.")
        cursor = conn.execute(query, params or ())
        columns = [description[0] for description in cursor.description or ()]
        raw_rows = cursor.fetchmany(max_rows + 1)
        if len(raw_rows) > max_rows:
            raise ValueError(
                f"Query exceeds the metadata helper limit of {max_rows} rows."
            )
        results = [dict(zip(columns, row)) for row in raw_rows]
        return results
    except sqlite3.Error as e:
        raise sqlite3.Error(f"Error executing query: {e}") from e


def get_schema(conn: sqlite3.Connection) -> Dict[str, List[Dict[str, str]]]:
    """
    Get the database schema including all tables and their columns.

    Args:
        conn: Active SQLite database connection

    Returns:
        Dict mapping table names to their column info from PRAGMA table_info.
    """
    schema: Dict[str, List[Dict[str, str]]] = {}

    tables = query_db(
        conn,
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
    )

    for table in tables:
        table_name = table["name"]
        columns = query_db(conn, f'PRAGMA table_info("{table_name}")')
        schema[table_name] = columns

    return schema


def load_column_descriptions(
    description_dir: str = DEFAULT_DESCRIPTION_DIR,
) -> Dict[str, List[Dict[str, str]]]:
    """
    Load BIRD column documentation CSVs shipped with the dataset.

    Returns a dict mapping table name (from filename) to rows of column metadata.
    Candidates may use this as a documentation corpus when building their agent.
    """
    root = Path(description_dir)
    if not root.is_dir():
        raise FileNotFoundError(
            f"Column description directory not found: {description_dir}\n" "Please run setup.sh first."
        )

    descriptions: Dict[str, List[Dict[str, str]]] = {}
    for csv_path in sorted(root.glob("*.csv")):
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            descriptions[csv_path.stem] = [dict(row) for row in reader]
    return descriptions


def get_rag_corpus(
    description_dir: str = DEFAULT_DESCRIPTION_DIR,
) -> List[Dict[str, str]]:
    """
    Return a flat list of column documentation records for indexing/search.

    Each record includes table, original_column_name, column_name, column_description,
    data_format, and value_description fields when present in the CSV.
    """
    corpus: List[Dict[str, str]] = []
    for table_name, rows in load_column_descriptions(description_dir).items():
        for row in rows:
            corpus.append(
                {
                    "table": table_name,
                    "original_column_name": row.get("original_column_name", ""),
                    "column_name": row.get("column_name", ""),
                    "column_description": row.get("column_description", ""),
                    "data_format": row.get("data_format", ""),
                    "value_description": row.get("value_description", ""),
                }
            )
    return corpus


def print_table_schema(conn: sqlite3.Connection, table_name: Optional[str] = None) -> None:
    """
    Print a formatted view of the database schema.

    Args:
        conn: Active SQLite database connection
        table_name: Optional specific table name to display. If None, shows all tables.
    """
    schema = get_schema(conn)

    if table_name:
        if table_name not in schema:
            print(f"Error: Table '{table_name}' not found in database.")
            print(f"Available tables: {', '.join(schema.keys())}")
            return
        tables_to_print = {table_name: schema[table_name]}
    else:
        tables_to_print = schema

    print("\n" + "=" * 100)
    print(f"DATABASE SCHEMA - {len(schema)} tables")
    print("=" * 100)

    if not table_name:
        print("\nTables:")
        for i, tbl in enumerate(schema.keys(), 1):
            print(f"  {i}. {tbl}")
        print()

    for tbl_name, columns in tables_to_print.items():
        print("\n" + "-" * 100)
        print(f"Table: {tbl_name} ({len(columns)} columns)")
        print("-" * 100)
        print(f"{'Column':<30} {'Type':<20} {'Nullable':<12} {'PK':<5} {'Default':<15}")
        print("-" * 100)

        for col in columns:
            nullable = "NULL" if col["notnull"] == 0 else "NOT NULL"
            pk = "Y" if col["pk"] > 0 else ""
            default = str(col["dflt_value"]) if col["dflt_value"] is not None else ""
            print(f"{col['name']:<30} {col['type']:<20} {nullable:<12} {pk:<5} {default:<15}")

    print("=" * 100 + "\n")
