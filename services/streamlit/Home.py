"""Run the Streamlit multipage entrypoint with ``st.navigation``.

Start with ``streamlit run services/streamlit/Home.py``. It registers Home plus
every numbered page found under ``services/streamlit/pages/`` and relates to
``app/ui.py`` and ``app/helpers.py``. The app holds no database credentials.
"""

from pathlib import Path
from typing import Any

import streamlit as st

PAGES_DIR = Path(__file__).parent / "pages"


def home() -> None:
    """Render the welcome page with the global filters."""
    from services.streamlit.app import ui

    st.title("Invent Market Tracker")
    filters = ui.global_filters()
    st.caption(f"City: {filters.city} | Service line: {filters.service_line}")


def build_pages() -> list[Any]:
    """Return Home followed by the page files that exist, in numeric order."""
    pages: list[Any] = [st.Page(home, title="Home", default=True)]
    for path in sorted(PAGES_DIR.glob("[0-9]*_*.py")):
        title = path.stem.split("_", 1)[1].replace("_", " ")
        pages.append(st.Page(str(path), title=title))
    return pages


if __name__ == "__main__" or st.runtime.exists():
    st.set_page_config(page_title="Invent Market Tracker", layout="wide")
    st.navigation(build_pages()).run()
