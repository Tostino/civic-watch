"""Postgres types on the wire.

Both HTTP surfaces serialise rows straight out of psycopg, and both meet the
same two types json.dumps will not touch. It lives here rather than in either
one because the second surface (MCP, web/mcp_server.py) arrived after the
first and copying six lines is how two surfaces start disagreeing about what
a timestamp looks like.
"""
import datetime
import decimal


def jsonable(o):
    """Postgres returns real types where SQLite returned strings.

    timestamptz arrives as datetime and numeric as Decimal, neither of which
    json.dumps will touch. Handled here rather than at each call site, so a
    column added later cannot silently 500 an endpoint.
    """
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    raise TypeError(f"{type(o).__name__} is not JSON serializable")
