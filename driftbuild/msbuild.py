"""Import the native subset of upstream Visual C++ project files."""

from __future__ import annotations

import re
import shlex
import xml.etree.ElementTree as element_tree
from pathlib import Path

from driftbuild.errors import ConfigurationError
from driftbuild.model import BuildConfig, MsbuildProject, ProjectSpec, TargetKind
from driftbuild.project import ProjectApi

_NAMESPACE = {"msbuild": "http://schemas.microsoft.com/developer/msbuild/2003"}
_DEFAULT_WINDOWS_LIBRARIES = (
    "kernel32.lib",
    "user32.lib",
    "gdi32.lib",
    "winmm.lib",
    "imm32.lib",
    "ole32.lib",
    "oleaut32.lib",
    "version.lib",
    "uuid.lib",
    "advapi32.lib",
    "setupapi.lib",
    "shell32.lib",
)
_KINDS: dict[str, TargetKind] = {
    "Application": "executable",
    "DynamicLibrary": "shared_library",
    "StaticLibrary": "static_library",
}


def _configuration(config: BuildConfig) -> tuple[str, str]:
    platforms = {"x86": "Win32", "x86_64": "x64", "arm64": "ARM64"}
    platform = platforms.get(config.architecture)
    if platform is None:
        raise ConfigurationError(f"MSBuild import does not support architecture {config.architecture}")
    return ("Debug" if config.build_type == "debug" else "Release", platform)


def _matches(condition: str | None, configuration: str, platform: str) -> bool:
    if condition is None:
        return True
    compact = condition.replace(" ", "").replace('"', "'")
    expected = f"'$(Configuration)|$(Platform)'=='{configuration}|{platform}'"
    return compact == expected


def _values(text: str | None) -> list[str]:
    if text is None:
        return []
    return [
        value.strip()
        for value in text.split(";")
        if value.strip()
        and not value.strip().startswith("%(")
        and re.fullmatch(r"\$\([A-Za-z0-9_]+\)", value.strip()) is None
    ]


def _package_path(root: Path, project_directory: Path, value: str) -> Path:
    expanded = re.sub(r"\$\(ProjectDir\)", lambda _match: str(project_directory) + "/", value, flags=re.IGNORECASE)
    if "$(" in expanded:
        raise ConfigurationError(f"Unsupported MSBuild path expression: {value}")
    path = Path(expanded.replace("\\", "/"))
    resolved = path.resolve() if path.is_absolute() else (project_directory / path).resolve()
    try:
        return resolved.relative_to(root.resolve())
    except ValueError as error:
        raise ConfigurationError(f"MSBuild path escapes the package root: {value}") from error


def _unique(values: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


def project_import(root: Path, config: BuildConfig, build: MsbuildProject) -> ProjectSpec:
    """Translate one MSBuild C/C++ project into the native Drift graph."""
    path = (root / build.project_file).resolve()
    try:
        path.relative_to(root.resolve())
        document = element_tree.parse(path)
    except (ValueError, OSError, element_tree.ParseError) as error:
        raise ConfigurationError(f"Cannot load MSBuild project {build.project_file}: {error}") from error

    configuration, platform = _configuration(config)
    xml_root = document.getroot()
    project_name = xml_root.findtext(".//msbuild:ProjectName", namespaces=_NAMESPACE) or path.stem
    configuration_type: str | None = None
    include_values: list[str] = []
    definitions: list[str] = []
    compile_arguments: list[str] = []
    libraries = list(_DEFAULT_WINDOWS_LIBRARIES)

    for group in xml_root:
        if not _matches(group.get("Condition"), configuration, platform):
            continue
        value = group.findtext("msbuild:ConfigurationType", namespaces=_NAMESPACE)
        if value is not None:
            configuration_type = value.strip()
        include_values.extend(_values(group.findtext("msbuild:IncludePath", namespaces=_NAMESPACE)))
        compile_settings = group.find("msbuild:ClCompile", _NAMESPACE)
        if compile_settings is None:
            continue
        include_values.extend(
            _values(compile_settings.findtext("msbuild:AdditionalIncludeDirectories", namespaces=_NAMESPACE))
        )
        definitions.extend(_values(compile_settings.findtext("msbuild:PreprocessorDefinitions", namespaces=_NAMESPACE)))
        options = compile_settings.findtext("msbuild:AdditionalOptions", namespaces=_NAMESPACE)
        if options is not None:
            compile_arguments.extend(value for value in shlex.split(options, posix=False) if not value.startswith("%("))
        warnings = _values(compile_settings.findtext("msbuild:DisableSpecificWarnings", namespaces=_NAMESPACE))
        compile_arguments.extend(f"/wd{warning}" for warning in warnings)
        link_settings = group.find("msbuild:Link", _NAMESPACE)
        if link_settings is not None:
            libraries.extend(_values(link_settings.findtext("msbuild:AdditionalDependencies", namespaces=_NAMESPACE)))

    imported_kind = _KINDS.get(configuration_type or "")
    kind = build.kind or imported_kind
    if kind is None:
        raise ConfigurationError(f"Unsupported MSBuild ConfigurationType: {configuration_type}")
    if imported_kind == "shared_library" and kind == "static_library":
        definitions = [value for value in definitions if value != "DLL_EXPORT"]
    definitions.extend(build.defines)

    project_directory = path.parent
    sources = tuple(
        _package_path(root, project_directory, element.attrib["Include"])
        for element in xml_root.findall(".//msbuild:ClCompile", _NAMESPACE)
        if "Include" in element.attrib
    )
    headers = tuple(
        _package_path(root, project_directory, element.attrib["Include"])
        for element in xml_root.findall(".//msbuild:ClInclude", _NAMESPACE)
        if "Include" in element.attrib
    )
    include_dirs = tuple(_package_path(root, project_directory, value) for value in include_values)
    api = ProjectApi(root, config)
    system = api.dependency("msbuild-windows-sdk", link_arguments=_unique(libraries))
    constructor = {
        "static_library": api.static_library,
        "shared_library": api.shared_library,
        "executable": api.executable,
    }[kind]
    target = constructor(
        project_name,
        sources=sources,
        public_headers=headers,
        include_dirs=include_dirs,
        defines=_unique(definitions),
        compile_arguments=_unique(compile_arguments),
        dependencies=(system,),
    )
    return api.project(project_name, defaults=(target,))
