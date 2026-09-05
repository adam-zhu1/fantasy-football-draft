import re
import unicodedata

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.I)

TEAM_FIX = {"JAC": "JAX", "WSH": "WAS", "LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}


ALIASES = {"kennygainwell": "kennethgainwell", "devonachane": "devonachane", "hollywoodbrown": "marquisebrown",
           "chigoziemokonkwo": "chigokonkwo", "joshuapalmer": "joshpalmer", "gabedavis": "gabrieldavis"}


def norm_name(name: str) -> str:
    """Canonical join key: lowercase, ascii, no suffixes/punctuation/spaces."""
    if name is None:
        return ""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = s.lower()
    s = _SUFFIX.sub("", s)
    s = re.sub(r"[^a-z]", "", s)
    return ALIASES.get(s, s)


def norm_team(t):
    if t is None:
        return ""
    t = str(t).strip().upper()
    return TEAM_FIX.get(t, t)
