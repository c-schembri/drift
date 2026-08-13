"""Xcode legacy project generation backed by Drift."""

from __future__ import annotations

import hashlib
from pathlib import Path

from driftbuild.model import ProjectSpec


def _identifier(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24].upper()


def generate(project: ProjectSpec, root: Path, output: Path | None = None) -> Path:
    """Generate an Xcode project whose targets invoke Drift directly."""
    bundle = output or root / f"{project.name}.xcodeproj"
    bundle.mkdir(parents=True, exist_ok=True)
    targets = [target for target in project.targets if not target.name.startswith("__drift_package_")]
    target_ids = [_identifier("target:" + target.name) for target in targets]
    product_group = _identifier("products")
    main_group = _identifier("main")
    project_id = _identifier("project:" + project.name)
    configuration = _identifier("configuration")
    configuration_list = _identifier("configuration-list")
    lines = ["// !$*UTF8*$!", "{", "\tarchiveVersion = 1;", "\tobjectVersion = 56;", "\tobjects = {"]
    for target, identifier in zip(targets, target_ids, strict=True):
        lines.append(
            f"\t\t{identifier} = {{isa = PBXLegacyTarget; buildArgumentsString = \"drift build {target.name}\"; "
            f'buildToolPath = "/usr/bin/env"; buildWorkingDirectory = "{root.as_posix()}"; '
            f"dependencies = (); name = {target.name}; passBuildSettingsInEnvironment = 1; productName = {target.name}; }};"
        )
    lines.extend(
        (
            f"\t\t{main_group} = {{isa = PBXGroup; children = ({product_group},); sourceTree = \"<group>\"; }};",
            f"\t\t{product_group} = {{isa = PBXGroup; children = (); name = Products; sourceTree = \"<group>\"; }};",
            f"\t\t{configuration} = {{isa = XCBuildConfiguration; buildSettings = {{}}; name = Debug; }};",
            f"\t\t{configuration_list} = {{isa = XCConfigurationList; buildConfigurations = ({configuration},); "
            "defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug; };",
            f"\t\t{project_id} = {{isa = PBXProject; buildConfigurationList = {configuration_list}; "
            f"compatibilityVersion = \"Xcode 14.0\"; mainGroup = {main_group}; productRefGroup = {product_group}; "
            f"projectDirPath = \"{root.as_posix()}\"; targets = ({', '.join(target_ids)},); }};",
            "\t};",
            f"\trootObject = {project_id};",
            "}",
        )
    )
    (bundle / "project.pbxproj").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return bundle
