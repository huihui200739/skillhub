"""Plugin validation sub-package.

Public entry-point: ``extract_plugin_metadata`` / ``strip_skill_publication_artifact``.
"""

from plugins_market.validation._pipeline import extract_plugin_metadata, strip_skill_publication_artifact

__all__ = ["extract_plugin_metadata", "strip_skill_publication_artifact"]
