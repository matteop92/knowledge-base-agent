"""Small localhost-only, read-only browser for the local knowledge schema."""

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import re
from urllib.parse import parse_qs, quote, urlencode, urlsplit
import os

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


PORT = 8765
TABLES = (
    "documents", "runs", "chunk_sets", "chunks", "artifacts", "contexts",
    "summaries", "embedding_specs", "embedding_vectors",
    "profiles", "active_runs", "corpus_state",
)
URL_PREFIX = "KNOWLEDGE_DATABASE_URL="
env_file = Path(__file__).resolve().parents[2] / ".env"
if os.environ.get("KNOWLEDGE_DATABASE_URL"):
    DATABASE_URL = os.environ["KNOWLEDGE_DATABASE_URL"]
else:
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    urls = [line[len(URL_PREFIX):] for line in lines if line.startswith(URL_PREFIX)]
    if len(urls) != 1 or not urls[0]:
        raise SystemExit("Set KNOWLEDGE_DATABASE_URL or add it once to the repository .env")
    DATABASE_URL = urls[0]


def connection():
    return psycopg.connect(
        DATABASE_URL,
        autocommit=True,
        options="-c default_transaction_read_only=on",
        row_factory=dict_row,
    )


def value_preview(value):
    if value is None:
        return "—"
    if isinstance(value, bytes):
        return f"<binary: {len(value):,} bytes>"
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, ensure_ascii=False)
    else:
        rendered = str(value)
    return rendered[:160] + ("…" if len(rendered) > 160 else "")


def value_full(column, value):
    if value is None:
        return "—"
    if isinstance(value, bytes):
        return f"<binary: {len(value):,} bytes; use the source_text column to read the document>"
    if column == "embedding":
        return "<numeric embedding vector; see embedding_specs for its model and dimensions>"
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    return str(value)


def layout(title, body):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · Local knowledge DB</title>
<style>
body{{font:15px/1.5 system-ui,sans-serif;color:#1d2632;background:#f5f7fa;margin:0}}
header{{background:#18263a;color:white;padding:16px 5vw}}
header a{{color:#b9dcff;text-decoration:none}}
main{{max-width:1300px;margin:26px auto;padding:0 24px}}
h1{{margin:.2em 0 .4em;font-size:1.8rem}} h2{{margin-top:1.6em}}
.hint{{color:#556574}} .box{{background:white;border:1px solid #dce3ea;border-radius:9px;padding:16px;margin:14px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}}
.grid a{{background:white;border:1px solid #dce3ea;border-radius:8px;padding:13px;text-decoration:none;color:#155d96}}
.grid strong{{display:block;color:#243344;font-size:1.2rem}}
.scroll{{overflow:auto;background:white;border:1px solid #dce3ea;border-radius:9px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{border-bottom:1px solid #e4e9ee;padding:8px 10px;text-align:left;vertical-align:top;max-width:270px;overflow-wrap:anywhere}}
th{{position:sticky;top:0;background:#edf2f7}} tr:hover{{background:#f7fbff}}
a{{color:#1263a0}} pre{{white-space:pre-wrap;overflow-wrap:anywhere;margin:.3em 0;font:13px/1.5 ui-monospace,Consolas,monospace}}
.field{{border-bottom:1px solid #e4e9ee;padding:12px 0}} .field b{{display:block;margin-bottom:5px}}
.pager a{{margin-right:18px}}
form{{display:flex;flex-wrap:wrap;gap:10px;align-items:end}}
label{{display:grid;gap:4px;font-weight:600}}
select,input,button{{font:inherit;padding:8px;border:1px solid #aebdca;border-radius:5px}}
select{{max-width:min(100%,600px)}} input{{min-width:250px}} button{{background:#155d96;color:white;cursor:pointer}}
</style></head><body><header><a href="/">← All tables</a> · Local knowledge database · Read-only</header><main>{body}</main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def send_page(self, title, body, status=200):
        data = layout(title, body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlsplit(self.path)
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self.home()
            elif parsed.path == "/table":
                self.table(params)
            elif parsed.path == "/row":
                self.row(params)
            else:
                self.send_page("Not found", "<h1>Not found</h1>", 404)
        except Exception as exc:
            self.send_page("Error", "<h1>Could not load this view</h1><p>" + escape(str(exc)) + "</p>", 500)

    def home(self):
        with connection() as db:
            profile = db.execute("select profile_id from knowledge.corpus_state").fetchone()["profile_id"]
            counts = {name: db.execute(
                sql.SQL("select count(*) as n from knowledge.{}").format(sql.Identifier(name))
            ).fetchone()["n"] for name in TABLES}
            documents = db.execute("select relative_path from knowledge.documents order by relative_path").fetchall()
        table_links = "".join(f'<a href="/table?name={quote(name)}"><strong>{count:,}</strong>{escape(name)}</a>' for name, count in counts.items())
        items = "".join("<li>" + escape(row["relative_path"]) + "</li>" for row in documents)
        body = ("<h1>Browse the local database</h1>"
                "<p class='hint'>This is a live view of the private <code>knowledge</code> schema on this computer. Click a table, then a row to inspect its fields.</p>"
                f"<div class='box'><b>Selected search profile:</b> {escape(str(profile))}</div>"
                f"<div class='grid'>{table_links}</div><h2>Loaded source documents</h2><div class='box'><ul>{items}</ul></div>")
        self.send_page("All tables", body)

    def table(self, params):
        name = params.get("name", [""])[0]
        if name not in TABLES:
            self.send_page("Unknown table", "<h1>Unknown table</h1>", 404)
            return
        try:
            page = max(1, min(10000, int(params.get("page", ["1"])[0])))
        except ValueError:
            page = 1
        limit = 20
        with connection() as db:
            count = db.execute(sql.SQL("select count(*) as n from knowledge.{}").format(sql.Identifier(name))).fetchone()["n"]
            rows = db.execute(
                sql.SQL("select ctid::text as row_locator, * from knowledge.{} order by ctid limit %s offset %s").format(sql.Identifier(name)),
                (limit, (page - 1) * limit),
            ).fetchall()
        columns = [key for key in rows[0] if key != "row_locator"] if rows else []
        head = "<tr><th>Open</th>" + "".join("<th>" + escape(col) + "</th>" for col in columns) + "</tr>"
        body_rows = []
        for row in rows:
            link = f'/row?name={quote(name)}&loc={quote(row["row_locator"])}'
            cells = "".join("<td>" + escape(value_preview(row[col])) + "</td>" for col in columns)
            body_rows.append(f'<tr><td><a href="{link}">View</a></td>{cells}</tr>')
        pager = '<div class="pager">'
        if page > 1:
            pager += f'<a href="/table?name={quote(name)}&page={page-1}">← Previous</a>'
        if page * limit < count:
            pager += f'<a href="/table?name={quote(name)}&page={page+1}">Next →</a>'
        pager += "</div>"
        self.send_page(name, f"<h1>{escape(name)}</h1><p class='hint'>{count:,} rows · page {page}</p><div class='scroll'><table>{head}{''.join(body_rows)}</table></div>{pager}")

    def row(self, params):
        name = params.get("name", [""])[0]
        locator = params.get("loc", [""])[0]
        if name not in TABLES or not re.fullmatch(r"\(\d+,\d+\)", locator):
            self.send_page("Unknown row", "<h1>Unknown row</h1>", 404)
            return
        with connection() as db:
            row = db.execute(
                sql.SQL("select * from knowledge.{} where ctid = %s::tid").format(sql.Identifier(name)),
                (locator,),
            ).fetchone()
        if row is None:
            self.send_page("Missing row", "<h1>Row is no longer available</h1>", 404)
            return
        fields = "".join(f'<div class="field"><b>{escape(col)}</b><pre>{escape(value_full(col, value))}</pre></div>' for col, value in row.items())
        self.send_page(f"{name} row", f'<h1>{escape(name)} row</h1><p><a href="/table?name={quote(name)}">← Back to table</a></p><div class="box">{fields}</div>')

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Local database viewer ready at http://127.0.0.1:{PORT}", flush=True)
    server.serve_forever()
