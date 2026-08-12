from pathlib import Path

from driftbuild.api import BuildConfig, ProjectApi
from driftbuild.model import Dependency
from driftbuild.msbuild import project_import


def test_msbuild_imports_selected_native_configuration(tmp_path: Path) -> None:
    project_directory = tmp_path / "VisualC" / "sample"
    project_directory.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "include").mkdir()
    (tmp_path / "src" / "sample.c").write_text("int sample(void) { return 1; }\n", encoding="utf-8")
    (tmp_path / "include" / "sample.h").write_text("int sample(void);\n", encoding="utf-8")
    (project_directory / "sample.vcxproj").write_text(
        r"""<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <PropertyGroup><ProjectName>sample</ProjectName></PropertyGroup>
  <PropertyGroup Condition="'$(Configuration)|$(Platform)'=='Debug|x64'">
    <ConfigurationType>DynamicLibrary</ConfigurationType>
    <IncludePath>$(ProjectDir)/../../src;$(IncludePath)</IncludePath>
  </PropertyGroup>
  <ItemDefinitionGroup Condition="'$(Configuration)|$(Platform)'=='Debug|x64'">
    <ClCompile>
      <AdditionalOptions>%(AdditionalOptions) /utf-8</AdditionalOptions>
      <AdditionalIncludeDirectories>$(ProjectDir)/../../include;%(AdditionalIncludeDirectories)</AdditionalIncludeDirectories>
      <PreprocessorDefinitions>DLL_EXPORT;_DEBUG;%(PreprocessorDefinitions)</PreprocessorDefinitions>
      <DisableSpecificWarnings>4100</DisableSpecificWarnings>
    </ClCompile>
    <Link><AdditionalDependencies>sample-system.lib;%(AdditionalDependencies)</AdditionalDependencies></Link>
  </ItemDefinitionGroup>
  <ItemGroup>
    <ClCompile Include="..\..\src\sample.c" />
    <ClInclude Include="..\..\include\sample.h" />
  </ItemGroup>
</Project>
""",
        encoding="utf-8",
    )
    config = BuildConfig("win32", architecture="x86_64", compiler="msvc", build_type="debug")
    api = ProjectApi(tmp_path, config)
    build = api.msbuild("VisualC/sample/sample.vcxproj", kind="static_library", defines=("SAMPLE_STATIC",))

    imported = project_import(tmp_path, config, build)
    target = imported.targets[0]

    assert target.name == "sample"
    assert target.kind == "static_library"
    assert target.sources == (Path("src/sample.c"),)
    assert target.public_headers == (Path("include/sample.h"),)
    assert target.include_dirs == (Path("src"), Path("include"))
    assert target.defines == ("_DEBUG", "SAMPLE_STATIC")
    assert target.compile_arguments == ("/utf-8", "/wd4100")
    assert isinstance(target.dependencies[0], Dependency)
    assert "user32.lib" in target.dependencies[0].link.arguments
    assert "sample-system.lib" in target.dependencies[0].link.arguments


def test_package_accepts_msbuild_description_without_local_overlay(tmp_path: Path) -> None:
    api = ProjectApi(tmp_path, BuildConfig("win32"))
    build = api.msbuild("VisualC/sample.vcxproj")
    api.package("sample", source=api.git("https://example.com/sample.git", "a" * 40), build=build)

    package = api.project("consumer").packages[0]

    assert package.overlay is None
    assert package.build == build
