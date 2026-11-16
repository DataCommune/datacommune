"""Shared SQL LIKE escaping helpers.

User input containing `%` or `_` widens LIKE/ILIKE patterns beyond the
intended match (audit QE-03 / LB-05 / AD-03). Values bound into a LIKE
pattern must go through escape_like() and the SQL must declare
`ESCAPE '\'` so the backslashes are honoured as escape characters.
"""

ESCAPE_CHAR = "\\"


def escape_like(value: str, escape_char: str = ESCAPE_CHAR) -> str:
    """Escape LIKE wildcards so the value matches literally.

    The escape character itself is doubled first, then `%` and `_` are
    prefixed with it. Pair with `LIKE ... ESCAPE '\\'` in SQL.
    """
    return (
        value.replace(escape_char, escape_char * 2)
        .replace("%", escape_char + "%")
        .replace("_", escape_char + "_")
    )
