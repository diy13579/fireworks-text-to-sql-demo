"use strict";

let database = null;
let SQL = null;

const WRITE_KEYWORDS = new Set([
  "ALTER", "ANALYZE", "ATTACH", "BEGIN", "COMMIT", "CREATE", "DELETE",
  "DETACH", "DROP", "INSERT", "PRAGMA", "REINDEX", "RELEASE",
  "ROLLBACK", "SAVEPOINT", "TRANSACTION", "UPDATE", "VACUUM"
]);

self.onmessage = async event => {
  const { id, type, payload } = event.data || {};
  try {
    if (type === "init") {
      const result = await initialize(payload);
      self.postMessage({ id, ok: true, result });
      return;
    }
    if (type === "execute") {
      const result = execute(payload);
      self.postMessage({ id, ok: true, result });
      return;
    }
    throw new Error(`Unknown worker operation: ${type}`);
  } catch (error) {
    self.postMessage({
      id,
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    });
  }
};

async function initialize(payload) {
  if (!SQL) {
    const base = payload.sqlJsBase.replace(/\/$/, "");
    importScripts(`${base}/sql-wasm.js`);
    SQL = await initSqlJs({ locateFile: file => `${base}/${file}` });
  }

  let response = null;
  let selectedUrl = null;
  for (const url of payload.databaseUrls) {
    try {
      const candidate = await fetch(url, { cache: "force-cache" });
      if (candidate.ok) {
        response = candidate;
        selectedUrl = url;
        break;
      }
    } catch {
      // Try the next relative path.
    }
  }
  if (!response) {
    throw new Error("The demo database could not be downloaded.");
  }

  const bytes = new Uint8Array(await response.arrayBuffer());
  database = new SQL.Database(bytes);
  database.run("PRAGMA query_only = ON");
  return { bytes: bytes.byteLength, url: selectedUrl };
}

function execute(payload) {
  if (!database) {
    throw new Error("The database is not ready.");
  }
  const sql = normalizeAndValidate(payload.sql);
  const maxRows = Math.max(1, Number(payload.maxRows) || 100);
  const maxBytes = Math.max(1, Number(payload.maxBytes) || 1_048_576);
  const boundedSql = `SELECT * FROM (\n${sql}\n) AS _bounded_query LIMIT ${maxRows + 1}`;
  const started = performance.now();
  const statements = database.exec(boundedSql);
  const executionMs = performance.now() - started;

  if (!statements.length) {
    return { rows: [], columns: [], truncated: false, bytes: 0, executionMs };
  }

  const result = statements[statements.length - 1];
  const columns = uniqueColumns(result.columns);
  const rows = [];
  let bytes = 0;
  let truncated = result.values.length > maxRows;

  for (const values of result.values.slice(0, maxRows)) {
    const row = Object.fromEntries(columns.map((column, index) => [column, values[index]]));
    const rowBytes = new TextEncoder().encode(JSON.stringify(row)).byteLength;
    if (rowBytes > maxBytes && rows.length === 0) {
      throw new Error("A single row exceeds the 1 MiB result limit.");
    }
    if (bytes + rowBytes > maxBytes) {
      truncated = true;
      break;
    }
    rows.push(row);
    bytes += rowBytes;
  }

  return { rows, columns, truncated, bytes, executionMs };
}

function normalizeAndValidate(sql) {
  if (typeof sql !== "string" || !sql.trim()) {
    throw new Error("Generated SQL is empty.");
  }
  if (sql.length > 20_000) {
    throw new Error("Generated SQL exceeds the 20,000-character limit.");
  }

  let normalized = sql.trim();
  if (normalized.endsWith(";")) {
    normalized = normalized.slice(0, -1).trim();
  }
  const structural = stripLiteralsAndComments(normalized);
  if (structural.includes(";")) {
    throw new Error("Exactly one SQL statement is allowed.");
  }
  const tokens = structural.match(/[A-Za-z_][A-Za-z0-9_]*/g) || [];
  const upper = tokens.map(token => token.toUpperCase());
  if (!["SELECT", "WITH"].includes(upper[0])) {
    throw new Error("Only SELECT and WITH queries are allowed.");
  }
  const forbidden = upper.find(token => WRITE_KEYWORDS.has(token));
  if (forbidden) {
    throw new Error(`Forbidden SQL operation: ${forbidden}`);
  }
  return normalized;
}

function stripLiteralsAndComments(sql) {
  let output = "";
  let index = 0;
  while (index < sql.length) {
    const char = sql[index];
    const next = sql[index + 1];
    if (char === "'" || char === '"') {
      const quote = char;
      output += " ";
      index += 1;
      while (index < sql.length) {
        if (sql[index] === quote && sql[index + 1] === quote) {
          index += 2;
          continue;
        }
        if (sql[index] === quote) {
          index += 1;
          break;
        }
        index += 1;
      }
      continue;
    }
    if (char === "-" && next === "-") {
      while (index < sql.length && sql[index] !== "\n") index += 1;
      output += "\n";
      continue;
    }
    if (char === "/" && next === "*") {
      index += 2;
      while (index < sql.length && !(sql[index] === "*" && sql[index + 1] === "/")) index += 1;
      index += 2;
      output += " ";
      continue;
    }
    output += char;
    index += 1;
  }
  return output;
}

function uniqueColumns(columns) {
  const counts = new Map();
  return columns.map(column => {
    const count = (counts.get(column) || 0) + 1;
    counts.set(column, count);
    return count === 1 ? column : `${column}_${count}`;
  });
}
