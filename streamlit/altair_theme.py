"""Altair theme derived from the active Streamlit dark/light theme.

st.context.theme only exposes which mode is active (`type`), not the actual
hex colors -- those live in .streamlit/config.toml under [theme.dark] /
[theme.light] and must be read per-mode via st.get_option.
"""

from __future__ import annotations

import altair as alt
import streamlit as st

_FALLBACK = {
    "dark": {
        "primary": "#a855f7",
        "bg": "#0d1117",
        "secondary_bg": "#161b22",
        "text": "#f0f6fc",
    },
    "light": {
        "primary": "#1976d2",
        "bg": "#ffffff",
        "secondary_bg": "#f5f5f5",
        "text": "#212121",
    },
}


def get_theme_colors() -> dict[str, str]:
    """Read config.toml colors for the client's active dark/light mode."""
    mode = getattr(st.context.theme, "type", None) or "dark"
    fallback = _FALLBACK[mode]
    return {
        "primary": st.get_option(f"theme.{mode}.primaryColor") or fallback["primary"],
        "bg": st.get_option(f"theme.{mode}.backgroundColor") or fallback["bg"],
        "secondary_bg": st.get_option(f"theme.{mode}.secondaryBackgroundColor")
        or fallback["secondary_bg"],
        "text": st.get_option(f"theme.{mode}.textColor") or fallback["text"],
    }


@alt.theme.register("whenwin", enable=True)
def _whenwin_altair_theme() -> alt.theme.ThemeConfig:
    """Custom Altair theme derived from the active Streamlit theme.

    Registered once at import via the decorator. The function body runs
    each time Altair applies the theme, so calling get_theme_colors() here
    picks up the current dark/light palette dynamically.
    """
    colors = get_theme_colors()
    return alt.theme.ThemeConfig(
        {
            "config": {
                "background": "transparent",
                "mark": {"color": colors["primary"]},
                "axis": {
                    "labelColor": colors["text"],
                    "titleColor": colors["text"],
                    "gridColor": colors["secondary_bg"],
                    "domainColor": colors["text"],
                    "tickColor": colors["text"],
                },
                "legend": {
                    "labelColor": colors["text"],
                    "titleColor": colors["text"],
                },
                "title": {"color": colors["text"]},
                "view": {"stroke": "transparent"},
                "range": {
                    "heatmap": [colors["secondary_bg"], colors["primary"]],
                },
            },
        }
    )
