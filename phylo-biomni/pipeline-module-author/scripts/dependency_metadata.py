"""Small package-runtime dependency facts used by static image checks."""

R_RUNTIME_DEPENDENCIES = {
    "tximport": {"jsonlite"},
}

R_PROVIDED_BY = {
    "tximport": {"tximeta"},
    "jsonlite": {"tximeta"},
}


def runtime_dependencies(package: str, language: str) -> set[str]:
    if language != "r":
        return set()
    return set(R_RUNTIME_DEPENDENCIES.get(package.lower(), set()))


def provided_by(package: str, language: str) -> set[str]:
    if language != "r":
        return set()
    return set(R_PROVIDED_BY.get(package.lower(), set()))
