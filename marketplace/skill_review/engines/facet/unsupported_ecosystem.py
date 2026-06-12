from __future__ import annotations

from skill_review.domain.system_facets import SkillCapabilityFact
from skill_review.domain.types import PackageFileEntry
from skill_review.engines.facet.coverage_gap import build_coverage_gap_fact

UNSUPPORTED_ECOSYSTEM_MARKERS = [
    ("go", {"go.mod", "go.sum"}, {".go"}),
    ("rust", {"Cargo.toml", "Cargo.lock"}, {".rs"}),
    ("jvm", {"pom.xml", "build.gradle", "build.gradle.kts"}, {".java", ".kt"}),
    ("ruby", {"Gemfile", "Gemfile.lock"}, {".rb"}),
    ("php", {"composer.json", "composer.lock"}, {".php"}),
    (".net", {"packages.lock.json"}, {".cs", ".csproj"}),
]
IGNORED_PATH_SEGMENTS = ["/node_modules/", "/vendor/", "/dist/", "/build/", "/.git/", "/__pycache__/"]


def collect_unsupported_ecosystem_facts(files: list[PackageFileEntry]) -> list[SkillCapabilityFact]:
    first_match_by_ecosystem: dict[str, str] = {}
    for file in files:
        ecosystem = detect_unsupported_ecosystem(file.path)
        if ecosystem and ecosystem not in first_match_by_ecosystem:
            first_match_by_ecosystem[ecosystem] = file.path
    return [
        build_coverage_gap_fact(
            extractor_id="unsupported-ecosystem",
            file_path=file_path,
            affects=["external_network", "third_party_dependencies"],
            reason="unsupported_ecosystem",
            detail=f"detected unsupported ecosystem {ecosystem} from {file_path}",
        )
        for ecosystem, file_path in first_match_by_ecosystem.items()
    ]


def detect_unsupported_ecosystem(file_path: str) -> str | None:
    normalized = "/" + file_path.replace("\\", "/").lstrip("/")
    if any(segment in normalized for segment in IGNORED_PATH_SEGMENTS):
        return None
    file_name = normalized.rsplit("/", 1)[-1]
    for ecosystem, marker_files, extensions in UNSUPPORTED_ECOSYSTEM_MARKERS:
        if file_name in marker_files or any(file_name.endswith(extension) for extension in extensions):
            return ecosystem
    return None
