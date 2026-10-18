"""Define the dependency-free Streamlit multipage entrypoint scaffold.

The file is intentionally free of a direct Streamlit import because the
current project dependencies do not include Streamlit. It relates to the
future pages under ``services/streamlit/pages/`` and is used as the stable
entrypoint location when the reporting dependency is introduced.
"""

from typing import Protocol


class StreamlitRuntime(Protocol):
    """Describe the small runtime surface needed by the placeholder page."""

    def set_page_config(self, *, page_title: str, layout: str) -> None:
        """Configure the page shell."""

    def title(self, body: str) -> None:
        """Render the page title."""

    def info(self, body: str) -> None:
        """Render a scaffold notice."""


def render_home(streamlit: StreamlitRuntime) -> None:
    """Render the minimal Home page once a Streamlit runtime is provided."""
    streamlit.set_page_config(page_title="Invent Market Tracker", layout="wide")
    streamlit.title("Invent Market Tracker")
    streamlit.info("Reporting pages will be implemented in a later frontend phase.")
