"""Visual Studio solution frontend for Drift's Ninja build graph."""

from __future__ import annotations

import os
import re
import subprocess
import uuid
import xml.etree.ElementTree as xml
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from driftbuild.errors import ConfigurationError
from driftbuild.graph import project_validate
from driftbuild.model import Artifact, Dependency, ProjectSpec, TargetDependency, TargetRef, TargetSpec

_MSBUILD_NAMESPACE = "http://schemas.microsoft.com/developer/msbuild/2003"
_VC_PROJECT_TYPE = "{BC8A1FFA-BEE3-4634-8014-F334798102B3}"
_GUID_NAMESPACE = uuid.UUID("31747168-1188-46e1-8237-5939fc79682c")
_SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}
_HEADER_SUFFIXES = {".h", ".hh", ".hpp", ".hxx", ".inl"}
_SAFE_TARGET_NAME = re.compile(r"[A-Za-z0-9_.-]+")


def _dependency_target_name(dependency: TargetDependency) -> str:
    assert isinstance(dependency.target, TargetRef)
    return dependency.target.name


@dataclass(frozen=True)
class VisualStudioResult:
    """Generated solution and project file locations."""

    solution: Path
    projects: Mapping[str, Path]


def _guid(project_name: str, value: str) -> str:
    return "{" + str(uuid.uuid5(_GUID_NAMESPACE, f"{project_name}:{value}")).upper() + "}"


def _filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def _windows_relative(source: Path, destination: Path) -> str:
    return os.path.relpath(source, destination).replace("/", "\\")


def _project_path(source: Path, output_root: Path) -> str:
    return f"$(ProjectDir){_windows_relative(source, output_root)}"


def _element(parent: xml.Element, name: str, text: str | None = None, **attributes: str) -> xml.Element:
    element = xml.SubElement(parent, name, attributes)
    element.text = text
    return element


def _target_dependencies(target: TargetSpec) -> set[str]:
    dependencies = {_dependency_target_name(item) for item in target.dependencies if isinstance(item, TargetDependency)}
    dependencies.update(item.name for item in target.objects)
    for value in (*target.sources, *target.runtime_files):
        if isinstance(value, Artifact):
            dependencies.add(value.target.name)
    if target.action is not None:
        for value in (*target.action.inputs, *target.action.implicit_inputs, *target.action.order_only):
            if isinstance(value, Artifact):
                dependencies.add(value.target.name)
    return dependencies


def _public_interface(
    targets: Mapping[str, TargetSpec], name: str, seen: set[str] | None = None
) -> tuple[list[Path], list[str]]:
    visited = set() if seen is None else seen
    if name in visited:
        return [], []
    visited.add(name)
    target = targets[name]
    includes = list(target.include_dirs)
    defines = list(target.defines)
    for dependency in target.dependencies:
        if isinstance(dependency, Dependency):
            includes.extend(dependency.compile.include_dirs)
            defines.extend(dependency.compile.defines)
        elif dependency.visibility == "public":
            child_includes, child_defines = _public_interface(targets, _dependency_target_name(dependency), visited)
            includes.extend(child_includes)
            defines.extend(child_defines)
    return includes, defines


def _compile_interface(target: TargetSpec, targets: Mapping[str, TargetSpec]) -> tuple[list[Path], list[str]]:
    includes = list(target.include_dirs)
    defines = list(target.defines)
    for dependency in target.dependencies:
        if isinstance(dependency, Dependency):
            includes.extend(dependency.compile.include_dirs)
            defines.extend(dependency.compile.defines)
        else:
            child_includes, child_defines = _public_interface(targets, _dependency_target_name(dependency))
            includes.extend(child_includes)
            defines.extend(child_defines)
    return includes, defines


def _input_paths(target: TargetSpec) -> tuple[Path, ...]:
    values = (*target.sources, *target.public_headers, *target.private_headers)
    return tuple(value for value in values if isinstance(value, Path))


def _project_output(target: TargetSpec, root: Path, architecture: str, build_type: str) -> Path | None:
    build_root = root / ".drift" / "build" / f"win32-{architecture}-msvc-{build_type}"
    if target.outputs:
        return build_root / target.outputs[0]
    if target.kind == "executable":
        return build_root / "bin" / f"{target.name}.exe"
    if target.kind == "static_library":
        return build_root / "lib" / f"{target.name}.lib"
    if target.kind == "shared_library":
        return build_root / "bin" / f"{target.name}.dll"
    return None


def _command(
    root_relative: str,
    architecture: str,
    build_type: str,
    operation: str,
    target: str | None = None,
    values: Mapping[str, str] | None = None,
) -> str:
    root_argument = f'"$(SolutionDir){root_relative}"' if root_relative != "." else '"$(SolutionDir)"'

    def quote(value: str) -> str:
        if any(character in value for character in ("%", "\r", "\n")):
            raise ConfigurationError("Visual Studio command values cannot contain percent signs or newlines")
        rendered = subprocess.list2cmdline([value])
        return rendered if rendered.startswith('"') else f'"{rendered}"'

    value_arguments = " ".join(f"-D {quote(f'{name}={value}')}" for name, value in sorted((values or {}).items()))
    value_arguments = f" {value_arguments}" if value_arguments else ""
    command = (
        f"drift --root {root_argument} --compiler msvc --architecture {architecture} "
        f"--build-type {build_type}{value_arguments} {operation}"
    )
    return f"{command} {quote(target)}" if target is not None else command


def _project_xml(
    project_name: str,
    target_name: str,
    configurations: Mapping[str, Mapping[str, TargetSpec]],
    root: Path,
    output_root: Path,
    architecture: str,
    platform_name: str,
    filenames: Mapping[str, str],
    values: Mapping[str, str],
) -> xml.ElementTree:
    root_element = xml.Element(
        "Project", {"DefaultTargets": "Build", "ToolsVersion": "Current", "xmlns": _MSBUILD_NAMESPACE}
    )
    configuration_items = _element(root_element, "ItemGroup", Label="ProjectConfigurations")
    for configuration in configurations:
        item = _element(configuration_items, "ProjectConfiguration", Include=f"{configuration}|{platform_name}")
        _element(item, "Configuration", configuration)
        _element(item, "Platform", platform_name)

    globals_group = _element(root_element, "PropertyGroup", Label="Globals")
    _element(globals_group, "ProjectGuid", _guid(project_name, target_name))
    _element(globals_group, "Keyword", "MakeFileProj")
    _element(globals_group, "ProjectName", target_name)
    _element(globals_group, "RootNamespace", _filename(target_name))
    _element(root_element, "Import", Project="$(VCTargetsPath)\\Microsoft.Cpp.Default.props")

    root_relative = _windows_relative(root, output_root)
    for configuration in configurations:
        build_type = configuration.casefold()
        condition = f"'$(Configuration)|$(Platform)'=='{configuration}|{platform_name}'"
        settings = _element(root_element, "PropertyGroup", Condition=condition, Label="Configuration")
        _element(settings, "ConfigurationType", "Makefile")
        _element(settings, "UseDebugLibraries", "true" if build_type == "debug" else "false")
        _element(settings, "CharacterSet", "MultiByte")

    _element(root_element, "Import", Project="$(VCTargetsPath)\\Microsoft.Cpp.props")
    _element(root_element, "ImportGroup", Label="ExtensionSettings")
    _element(root_element, "ImportGroup", Label="Shared")

    for configuration, targets in configurations.items():
        build_type = configuration.casefold()
        target = targets[target_name]
        condition = f"'$(Configuration)|$(Platform)'=='{configuration}|{platform_name}'"
        properties = _element(root_element, "PropertyGroup", Condition=condition)
        build_command = _command(root_relative, architecture, build_type, "build", target_name, values)
        clean_command = _command(root_relative, architecture, build_type, "clean", target_name, values)
        _element(properties, "NMakeBuildCommandLine", build_command)
        _element(properties, "NMakeCleanCommandLine", clean_command)
        _element(properties, "NMakeReBuildCommandLine", f"{clean_command} && {build_command}")
        output = _project_output(target, root, architecture, build_type)
        if output is not None:
            _element(properties, "NMakeOutput", _project_path(output, output_root))
        includes, defines = _compile_interface(target, targets)
        include_text = ";".join(_project_path(root / value, output_root) for value in dict.fromkeys(includes))
        define_text = ";".join(dict.fromkeys(defines))
        _element(properties, "NMakeIncludeSearchPath", include_text)
        _element(properties, "NMakePreprocessorDefinitions", define_text)
        if target.kind == "executable" and output is not None:
            _element(properties, "LocalDebuggerCommand", _project_path(output, output_root))
            _element(properties, "LocalDebuggerWorkingDirectory", f"$(SolutionDir){root_relative}")
            _element(properties, "DebuggerFlavor", "WindowsLocalDebugger")

    file_items: dict[str, list[Path]] = {"ClCompile": [], "ClInclude": [], "None": []}
    inputs = {value for targets in configurations.values() for value in _input_paths(targets[target_name])}
    for value in sorted(inputs, key=lambda path: path.as_posix()):
        if value.suffix.casefold() in _SOURCE_SUFFIXES:
            item_type = "ClCompile"
        elif value.suffix.casefold() in _HEADER_SUFFIXES:
            item_type = "ClInclude"
        else:
            item_type = "None"
        file_items[item_type].append(value)
    for item_type, paths in file_items.items():
        if not paths:
            continue
        item_group = _element(root_element, "ItemGroup")
        for path in paths:
            _element(item_group, item_type, Include=_windows_relative(root / path, output_root))

    dependencies = sorted(
        {name for targets in configurations.values() for name in _target_dependencies(targets[target_name])}
    )
    if dependencies:
        references = _element(root_element, "ItemGroup")
        for dependency in dependencies:
            reference = _element(references, "ProjectReference", Include=filenames[dependency])
            _element(reference, "Project", _guid(project_name, dependency))
            _element(reference, "ReferenceOutputAssembly", "false")

    _element(root_element, "Import", Project="$(VCTargetsPath)\\Microsoft.Cpp.targets")
    _element(root_element, "ImportGroup", Label="ExtensionTargets")
    xml.indent(root_element, space="  ")
    return xml.ElementTree(root_element)


def _filters_xml(project_name: str, targets: Sequence[TargetSpec], root: Path, output_root: Path) -> xml.ElementTree:
    root_element = xml.Element("Project", {"ToolsVersion": "4.0", "xmlns": _MSBUILD_NAMESPACE})
    paths = sorted({path for target in targets for path in _input_paths(target)}, key=lambda path: path.as_posix())
    filters: set[str] = set()
    for path in paths:
        parent = path.parent
        while parent != Path(".") and parent != parent.parent:
            filters.add(parent.as_posix())
            parent = parent.parent
    if filters:
        filter_group = _element(root_element, "ItemGroup")
        for value in sorted(filters):
            filter_element = _element(filter_group, "Filter", Include=value.replace("/", "\\"))
            _element(filter_element, "UniqueIdentifier", _guid(project_name, f"filter:{value}"))
    grouped: dict[str, list[Path]] = {"ClCompile": [], "ClInclude": [], "None": []}
    for path in paths:
        if path.suffix.casefold() in _SOURCE_SUFFIXES:
            grouped["ClCompile"].append(path)
        elif path.suffix.casefold() in _HEADER_SUFFIXES:
            grouped["ClInclude"].append(path)
        else:
            grouped["None"].append(path)
    for item_type, items in grouped.items():
        if not items:
            continue
        group = _element(root_element, "ItemGroup")
        for path in items:
            item = _element(group, item_type, Include=_windows_relative(root / path, output_root))
            if path.parent != Path("."):
                _element(item, "Filter", path.parent.as_posix().replace("/", "\\"))
    xml.indent(root_element, space="  ")
    return xml.ElementTree(root_element)


def _write_xml(path: Path, tree: xml.ElementTree) -> None:
    root_element = tree.getroot()
    assert root_element is not None
    content = xml.tostring(root_element, encoding="utf-8", xml_declaration=True)
    if path.is_file() and path.read_bytes() == content:
        return
    path.write_bytes(content)


def _solution_text(
    project_name: str,
    names: Sequence[str],
    configurations: Sequence[str],
    platform_name: str,
    targets: Mapping[str, TargetSpec],
    filenames: Mapping[str, str],
    build_project_name: str,
    build_project_filename: str,
) -> str:
    lines = [
        "Microsoft Visual Studio Solution File, Format Version 12.00",
        "# Visual Studio Version 17",
        "VisualStudioVersion = 17.0.31903.59",
        "MinimumVisualStudioVersion = 10.0.40219.1",
    ]
    for name in names:
        guid = _guid(project_name, name)
        lines.append(f'Project("{_VC_PROJECT_TYPE}") = "{name}", "{filenames[name]}", "{guid}"')
        dependencies = sorted(_target_dependencies(targets[name]))
        if dependencies:
            lines.append("\tProjectSection(ProjectDependencies) = postProject")
            lines.extend(
                f"\t\t{_guid(project_name, dependency)} = {_guid(project_name, dependency)}"
                for dependency in dependencies
            )
            lines.append("\tEndProjectSection")
        lines.append("EndProject")
    build_guid = _guid(project_name, "__solution_build__")
    lines.append(f'Project("{_VC_PROJECT_TYPE}") = "{build_project_name}", "{build_project_filename}", "{build_guid}"')
    lines.append("EndProject")
    lines += ["Global", "\tGlobalSection(SolutionConfigurationPlatforms) = preSolution"]
    for configuration in configurations:
        lines.append(f"\t\t{configuration}|{platform_name} = {configuration}|{platform_name}")
    lines.append("\tEndGlobalSection")
    lines.append("\tGlobalSection(ProjectConfigurationPlatforms) = postSolution")
    for name in names:
        guid = _guid(project_name, name)
        for configuration in configurations:
            key = f"{guid}.{configuration}|{platform_name}"
            lines.append(f"\t\t{key}.ActiveCfg = {configuration}|{platform_name}")
    for configuration in configurations:
        key = f"{build_guid}.{configuration}|{platform_name}"
        lines.append(f"\t\t{key}.ActiveCfg = {configuration}|{platform_name}")
        lines.append(f"\t\t{key}.Build.0 = {configuration}|{platform_name}")
    lines += [
        "\tEndGlobalSection",
        "\tGlobalSection(SolutionProperties) = preSolution",
        "\t\tHideSolutionNode = FALSE",
        "\tEndGlobalSection",
        "EndGlobal",
        "",
    ]
    return "\r\n".join(lines)


def _build_project_xml(
    project_name: str,
    configurations: Sequence[str],
    root: Path,
    output_root: Path,
    architecture: str,
    platform_name: str,
    values: Mapping[str, str],
) -> xml.ElementTree:
    root_element = xml.Element(
        "Project", {"DefaultTargets": "Build", "ToolsVersion": "Current", "xmlns": _MSBUILD_NAMESPACE}
    )
    configuration_items = _element(root_element, "ItemGroup", Label="ProjectConfigurations")
    for configuration in configurations:
        item = _element(configuration_items, "ProjectConfiguration", Include=f"{configuration}|{platform_name}")
        _element(item, "Configuration", configuration)
        _element(item, "Platform", platform_name)
    globals_group = _element(root_element, "PropertyGroup", Label="Globals")
    _element(globals_group, "ProjectGuid", _guid(project_name, "__solution_build__"))
    _element(globals_group, "Keyword", "MakeFileProj")
    _element(globals_group, "ProjectName", f"{project_name} (build)")
    _element(root_element, "Import", Project="$(VCTargetsPath)\\Microsoft.Cpp.Default.props")
    for configuration in configurations:
        condition = f"'$(Configuration)|$(Platform)'=='{configuration}|{platform_name}'"
        settings = _element(root_element, "PropertyGroup", Condition=condition, Label="Configuration")
        _element(settings, "ConfigurationType", "Makefile")
        _element(settings, "UseDebugLibraries", "true" if configuration == "Debug" else "false")
    _element(root_element, "Import", Project="$(VCTargetsPath)\\Microsoft.Cpp.props")
    root_relative = _windows_relative(root, output_root)
    for configuration in configurations:
        build_type = configuration.casefold()
        condition = f"'$(Configuration)|$(Platform)'=='{configuration}|{platform_name}'"
        properties = _element(root_element, "PropertyGroup", Condition=condition)
        build_command = _command(root_relative, architecture, build_type, "build", values=values)
        clean_command = _command(root_relative, architecture, build_type, "clean", values=values)
        _element(properties, "NMakeBuildCommandLine", build_command)
        _element(properties, "NMakeCleanCommandLine", clean_command)
        _element(properties, "NMakeReBuildCommandLine", f"{clean_command} && {build_command}")
    _element(root_element, "Import", Project="$(VCTargetsPath)\\Microsoft.Cpp.targets")
    xml.indent(root_element, space="  ")
    return xml.ElementTree(root_element)


def generate(
    projects: Mapping[str, ProjectSpec],
    root: Path,
    output_root: Path,
    architecture: str,
    startup_target: str | None = None,
    values: Mapping[str, str] | None = None,
) -> VisualStudioResult:
    """Generate a deterministic Makefile-style Visual Studio solution."""
    root = root.resolve()
    output_root = output_root.resolve()
    if not projects:
        raise ConfigurationError("Visual Studio generation requires at least one configuration")
    platform_name = {"x86_64": "x64", "x86": "Win32", "arm64": "ARM64"}.get(architecture)
    if platform_name is None:
        raise ConfigurationError(f"Visual Studio does not support Drift architecture: {architecture}")
    configuration_values = dict(values or {})
    validated = {name.capitalize(): project_validate(project) for name, project in projects.items()}
    first_targets = next(iter(validated.values()))
    project_name = next(iter(projects.values())).name
    if any(character in project_name for character in ('"', "\r", "\n")):
        raise ConfigurationError("Visual Studio project names cannot contain quotes or newlines")
    if not _filename(project_name):
        raise ConfigurationError("Visual Studio project name does not form a valid filename")
    expected = {(name, target.kind) for name, target in first_targets.items()}
    for configuration, targets in validated.items():
        actual = {(name, target.kind) for name, target in targets.items()}
        if actual != expected:
            raise ConfigurationError(f"Visual Studio requires identical target names and kinds in {configuration}")
    if any(project.name != project_name for project in projects.values()):
        raise ConfigurationError("Visual Studio requires one project name across configurations")
    invalid_names = sorted(name for name in first_targets if _SAFE_TARGET_NAME.fullmatch(name) is None)
    if invalid_names:
        raise ConfigurationError(f"Visual Studio target names must be filename-safe: {', '.join(invalid_names)}")
    if startup_target is not None:
        if startup_target not in first_targets:
            raise ConfigurationError(f"Unknown Visual Studio startup target: {startup_target}")
        if first_targets[startup_target].kind != "executable":
            raise ConfigurationError(f"Visual Studio startup target is not executable: {startup_target}")
    else:
        startup_target = next((name for name, target in first_targets.items() if target.kind == "executable"), None)

    output_root.mkdir(parents=True, exist_ok=True)
    filenames = {name: f"{_filename(name)}.vcxproj" for name in first_targets}
    if len(set(filenames.values())) != len(filenames):
        raise ConfigurationError("Target names collide as Visual Studio project filenames")
    ordered_names = sorted(first_targets)
    if startup_target is not None:
        ordered_names.remove(startup_target)
        ordered_names.insert(0, startup_target)

    generated: dict[str, Path] = {}
    for name in ordered_names:
        path = output_root / filenames[name]
        _write_xml(
            path,
            _project_xml(
                project_name,
                name,
                validated,
                root,
                output_root,
                architecture,
                platform_name,
                filenames,
                configuration_values,
            ),
        )
        _write_xml(
            path.with_suffix(".vcxproj.filters"),
            _filters_xml(
                project_name,
                tuple(targets[name] for targets in validated.values()),
                root,
                output_root,
            ),
        )
        generated[name] = path

    build_project_name = f"{project_name} (build)"
    build_project_filename = f"{_filename(project_name)}-build.vcxproj"
    if build_project_filename in filenames.values():
        raise ConfigurationError(f"Target name collides with Visual Studio build project: {build_project_filename}")
    _write_xml(
        output_root / build_project_filename,
        _build_project_xml(
            project_name,
            tuple(validated),
            root,
            output_root,
            architecture,
            platform_name,
            configuration_values,
        ),
    )

    solution = output_root / f"{_filename(project_name)}.sln"
    content = _solution_text(
        project_name,
        ordered_names,
        tuple(validated),
        platform_name,
        first_targets,
        filenames,
        build_project_name,
        build_project_filename,
    ).encode("utf-8")
    if not solution.is_file() or solution.read_bytes() != content:
        solution.write_bytes(content)
    return VisualStudioResult(solution, generated)
